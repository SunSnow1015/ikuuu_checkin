import io
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    import cv2
    import numpy as np
except ImportError as error:
    raise unittest.SkipTest('Install requirements-local.txt to test local image recognition') from error
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from captcha_images import RecognitionError, WordRecognizer, affine_score, match_icons, normalize_mask, unique_assignment


def png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def shape(kind):
    image = Image.new('RGBA', (64, 64))
    draw = ImageDraw.Draw(image)
    if kind == 'circle':
        draw.ellipse((10, 10, 54, 54), outline='black', width=4)
    elif kind == 'triangle':
        draw.line([(32, 7), (57, 54), (7, 54), (32, 7)], fill='black', width=4)
    else:
        draw.line([(10, 10), (50, 10), (50, 54), (10, 54), (10, 10)], fill='black', width=4)
        draw.line([(20, 10), (20, 30), (40, 30), (40, 10)], fill='black', width=4)
    return image


def scene():
    background = Image.new('RGBA', (300, 200), (70, 72, 74, 255))
    for kind, location, color, angle in (
        ('circle', (15, 25), (240, 20, 60, 255), 0),
        ('triangle', (115, 70), (30, 220, 70, 255), 20),
        ('book', (205, 115), (20, 180, 240, 255), -15),
    ):
        mask = shape(kind).rotate(angle, resample=Image.Resampling.BICUBIC)
        colored = Image.new('RGBA', mask.size, color)
        colored.putalpha(mask.getchannel('A'))
        background.alpha_composite(colored, location)
    return background


class ImageTests(unittest.TestCase):
    def test_rotated_shapes_follow_prompt_order(self):
        points, size = match_icons(png(scene()), [png(shape(kind)) for kind in ('book', 'circle', 'triangle')])
        self.assertEqual(size, (300, 200))
        for actual, (kind, location, angle) in zip(points, (
            ('book', (205, 115), -15), ('circle', (15, 25), 0), ('triangle', (115, 70), 20),
        )):
            rotated = shape(kind).rotate(angle, resample=Image.Resampling.BICUBIC)
            left, top, right, bottom = rotated.getchannel('A').getbbox()
            expected = (location[0] + (left + right) / 2, location[1] + (top + bottom) / 2)
            self.assertLess(abs(actual[0] - expected[0]), 2)
            self.assertLess(abs(actual[1] - expected[1]), 2)

    def test_affine_matching_allows_centered_anisotropic_scaling(self):
        reference = np.zeros((80, 80), dtype=np.uint8)
        reference[10:70, 30:35] = 1
        reference[10:15, 30:48] = 1
        reference[35:40, 30:45] = 1
        reference[65:70, 30:48] = 1
        matrix = np.array([[1.7, 0, -28], [0, 1, 0]], dtype=np.float32)
        candidate = cv2.warpAffine(reference, matrix, (80, 80), flags=cv2.INTER_NEAREST)
        self.assertGreater(affine_score(reference, candidate), 0.9)

    def test_affine_matching_rejects_degenerate_or_displaced_alignment(self):
        mask = normalize_mask((np.array(shape('triangle'))[:, :, 3] > 0).astype(np.uint8))
        for matrix in (
            [[0.3, 0, 28], [0, 1, 0]], [[2.5, 0, -60], [0, 1, 0]],
            [[-1, 0, 80], [0, 1, 0]], [[1, 0, 20], [0, 1, 0]],
        ):
            with self.subTest(matrix=matrix):
                with mock.patch('captcha_images.cv2.findTransformECC', return_value=(1, np.array(matrix, dtype=np.float32))):
                    self.assertEqual(affine_score(mask, mask), 0)

    def test_assignment_does_not_reuse_targets(self):
        scores = np.array([[0.97, 0.9, 0.1], [0.96, 0.1, 0.78]])
        result = unique_assignment(scores)
        self.assertEqual(result, (1, 0))

    def test_ambiguous_assignment_is_rejected(self):
        with self.assertRaisesRegex(RecognitionError, '歧义'):
            unique_assignment(np.array([[0.9, 0.9], [0.9, 0.9]]))

    def test_low_score_is_rejected(self):
        with self.assertRaisesRegex(RecognitionError, '置信度'):
            unique_assignment(np.array([[0.5, 0.3]]))

    def test_shared_matches_do_not_change_ambiguity_gap(self):
        self.assertEqual(unique_assignment(np.array([[0.9, 0.84]])), (0,))
        scores = np.array([[0.9, 0.84, 0, 0], [0, 0, 0.99, 0], [0, 0, 0, 0.99]])
        self.assertEqual(unique_assignment(scores), (0, 2, 3))
        scores[0, 1] = 0.88
        with self.assertRaisesRegex(RecognitionError, '歧义'):
            unique_assignment(scores)

    def test_ambiguity_is_checked_per_changed_target(self):
        with self.assertRaisesRegex(RecognitionError, '歧义'):
            unique_assignment(np.array([[0.9, 0.87], [0.87, 0.9]]))

    def test_blank_or_insufficient_targets_fail(self):
        with self.assertRaises(RecognitionError):
            normalize_mask(np.zeros((80, 80), dtype=np.uint8))
        with self.assertRaises(RecognitionError):
            match_icons(png(Image.new('RGB', (300, 200), 'white')), [png(shape('circle'))])

    def test_word_requires_model_agreement(self):
        recognizer = WordRecognizer.__new__(WordRecognizer)
        recognizer.ocr = mock.Mock()
        recognizer.beta_ocr = mock.Mock()
        recognizer.ocr.classification.return_value = '明'
        recognizer.beta_ocr.classification.return_value = '日'
        self.assertIsNone(recognizer.agreed_character(np.array(shape('circle'))))
        recognizer.beta_ocr.classification.return_value = '明'
        self.assertEqual(recognizer.agreed_character(np.array(shape('circle'))), '明')

    def test_word_matching_preserves_prompt_order(self):
        recognizer = WordRecognizer.__new__(WordRecognizer)
        recognizer.detector = mock.Mock()
        recognizer.detector.detection.return_value = [[0, 0, 40, 40], [50, 50, 90, 90]]
        recognizer.agreed_character = mock.Mock(side_effect=['明', '天'] + ['天'] * 7 + ['明'] * 7)
        points, size = recognizer.match(png(scene()), [png(shape('circle')), png(shape('book'))])
        self.assertEqual(points, [(70, 70), (20, 20)])
        self.assertEqual(size, (300, 200))

    def test_word_duplicates_are_rejected(self):
        recognizer = WordRecognizer.__new__(WordRecognizer)
        recognizer.agreed_character = mock.Mock(return_value='明')
        with self.assertRaises(RecognitionError):
            recognizer.match(png(scene()), [png(shape('circle')), png(shape('book'))])

    def test_word_unknown_target_is_rejected(self):
        recognizer = WordRecognizer.__new__(WordRecognizer)
        recognizer.detector = mock.Mock()
        recognizer.detector.detection.return_value = [[0, 0, 40, 40]]
        recognizer.agreed_character = mock.Mock(side_effect=['明'] + [None] * 7)
        with self.assertRaisesRegex(RecognitionError, '不完整'):
            recognizer.match(png(scene()), [png(shape('circle'))])


if __name__ == '__main__':
    unittest.main()
