import io
import itertools

import cv2
import numpy as np
from PIL import Image


class RecognitionError(ValueError):
    pass


def read_image(content):
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 4_000_000:
            raise RecognitionError('验证码图片尺寸过大')
        return np.array(image.convert('RGBA'))


def foreground(image):
    if np.any(image[:, :, 3] < 128):
        return (image[:, :, 3] > 80).astype(np.uint8)
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if mask.mean() > 0.5:
        mask = 1 - mask
    return mask


def normalize_mask(mask, size=80):
    points = cv2.findNonZero(mask.astype(np.uint8))
    if points is None:
        raise RecognitionError('提示图片为空')
    x, y, width, height = cv2.boundingRect(points)
    crop = mask[y:y + height, x:x + width]
    scale = (size - 8) / max(width, height)
    width, height = max(1, round(width * scale)), max(1, round(height * scale))
    scaled = cv2.resize(crop, (width, height), interpolation=cv2.INTER_NEAREST)
    result = np.zeros((size, size), dtype=np.uint8)
    left, top = (size - width) // 2, (size - height) // 2
    result[top:top + height, left:left + width] = scaled
    return result


def colored_candidates(image, saturation=120, value=120):
    hsv = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    bright = (hsv[:, :, 1] >= saturation) & (hsv[:, :, 2] >= value)
    histogram = np.bincount(hue[bright], minlength=180).astype(float)
    candidates = []
    for _ in range(8):
        center = int(histogram.argmax())
        if histogram[center] < 15:
            break
        distances = np.abs(np.arange(180) - center)
        distances = np.minimum(distances, 180 - distances)
        histogram[distances <= 12] = 0
        delta = np.abs(hue - center)
        mask = (bright & (np.minimum(delta, 180 - delta) <= 12)).astype(np.uint8)
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        keep = stats[:, cv2.CC_STAT_AREA] >= 8
        keep[0] = False
        mask = keep[labels].astype(np.uint8)
        joined = cv2.dilate(mask, np.ones((5, 5), dtype=np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(joined)
        for index in range(1, count):
            x, y, width, height, _ = stats[index]
            isolated = mask[y:y + height, x:x + width] * (labels[y:y + height, x:x + width] == index)
            if not (12 <= width <= image.shape[1] * 0.55 and 12 <= height <= image.shape[0] * 0.7):
                continue
            if isolated.sum() < 50:
                continue
            points = cv2.findNonZero(isolated.astype(np.uint8))
            dx, dy, inner_width, inner_height = cv2.boundingRect(points)
            box = (int(x + dx), int(y + dy), int(x + dx + inner_width), int(y + dy + inner_height))
            if any(box_iou(box, candidate[0]) > 0.65 for candidate in candidates):
                continue
            candidates.append((box, normalize_mask(isolated)))
    return sorted(candidates, key=lambda candidate: candidate[0])


def box_iou(first, second):
    intersection = max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(0, min(first[3], second[3]) - max(first[1], second[1]))
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / (first_area + second_area - intersection)


def rotated_masks(mask):
    normalized = normalize_mask(mask)
    size = normalized.shape[0]
    for angle in range(-180, 180, 5):
        matrix = cv2.getRotationMatrix2D((size / 2, size / 2), angle, 1)
        matrix[:, 2] += size / 2
        rotated = cv2.warpAffine(normalized, matrix, (size * 2, size * 2), flags=cv2.INTER_NEAREST)
        yield normalize_mask(rotated)


def mask_similarity(first, second, first_distance=None, second_distance=None):
    if not first.any() or not second.any():
        return 0.0
    if first_distance is None:
        first_distance = cv2.distanceTransform(1 - first, cv2.DIST_L2, 3)
    if second_distance is None:
        second_distance = cv2.distanceTransform(1 - second, cv2.DIST_L2, 3)
    forward = np.exp(-second_distance[first > 0] / 2.5).mean()
    backward = np.exp(-first_distance[second > 0] / 2.5).mean()
    return float((forward + backward) / 2)


def affine_score(reference, candidate):
    matrix = np.eye(2, 3, dtype=np.float32)
    try:
        _, matrix = cv2.findTransformECC(
            reference.astype(np.float32), candidate.astype(np.float32), matrix,
            cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 0.0001), None, 5,
        )
    except cv2.error:
        return 0.0
    scales = np.linalg.svd(matrix[:, :2], compute_uv=False)
    center = np.array([reference.shape[1] / 2, reference.shape[0] / 2])
    displacement = matrix[:, :2] @ center + matrix[:, 2] - center
    if min(scales) < 0.5 or max(scales) > 2 or np.linalg.det(matrix[:, :2]) <= 0 or np.max(np.abs(displacement)) > min(reference.shape) * 0.15:
        return 0.0
    aligned = cv2.warpAffine(candidate, matrix, (reference.shape[1], reference.shape[0]), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    return mask_similarity(reference, aligned)


def shape_scores(prompts, candidates):
    distances = [cv2.distanceTransform(1 - mask, cv2.DIST_L2, 3) for _, mask in candidates]
    scores = np.zeros((len(prompts), len(candidates)))
    for row, prompt in enumerate(prompts):
        variants = list(rotated_masks(foreground(prompt)))
        variant_distances = [cv2.distanceTransform(1 - mask, cv2.DIST_L2, 3) for mask in variants]
        for column, (_, candidate) in enumerate(candidates):
            ranked = sorted(((mask_similarity(rotated, candidate, variant_distances[index], distances[column]), index) for index, rotated in enumerate(variants)), reverse=True)
            score = ranked[0][0]
            for _, index in ranked[:3]:
                score = max(score, affine_score(variants[index], candidate))
            scores[row, column] = score
    return scores


def unique_assignment(scores, threshold=0.72, margin=0.035):
    rows, columns = scores.shape
    if not 1 <= rows <= 5 or not rows <= columns <= 12:
        raise RecognitionError('可用目标数量与提示不匹配')
    options = []
    for assignment in itertools.permutations(range(columns), rows):
        values = scores[np.arange(rows), assignment]
        options.append((float(values.sum()), assignment))
    options.sort(reverse=True)
    best, assignment = options[0]
    if np.min(scores[np.arange(rows), assignment]) < threshold:
        raise RecognitionError('图形匹配置信度不足，不提交点击结果')
    for score, alternative in options[1:]:
        changed = sum(first != second for first, second in zip(assignment, alternative))
        # Shared matches must not dilute the confidence gap of disputed targets.
        if (best - score) / changed < margin:
            raise RecognitionError('图形匹配存在歧义，不提交点击结果')
    return assignment


def match_icons(background, prompts):
    image = read_image(background)
    questions = [read_image(prompt) for prompt in prompts]
    candidates = colored_candidates(image)
    if len(candidates) < len(questions):
        raise RecognitionError('未检测到足够的彩色图形目标')
    assignment = unique_assignment(shape_scores(questions, candidates))
    return [((candidates[index][0][0] + candidates[index][0][2]) / 2,
             (candidates[index][0][1] + candidates[index][0][3]) / 2) for index in assignment], (image.shape[1], image.shape[0])


def png_on_white(image, angle=0):
    rgba = Image.fromarray(image)
    white = Image.new('RGBA', rgba.size, 'white')
    white.alpha_composite(rgba)
    result = white.convert('RGB').rotate(angle, expand=True, fillcolor='white')
    padded = Image.new('RGB', (result.width + 16, result.height + 16), 'white')
    padded.paste(result, (8, 8))
    output = io.BytesIO()
    padded.save(output, format='PNG')
    return output.getvalue()


class WordRecognizer:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR()

    def _results(self, content):
        results, _ = self.engine(content, use_cls=False)
        return results or []

    def _prompt_character(self, content):
        results, _ = self.engine(content, use_det=False, use_cls=False)
        texts = [text.strip() for text, score in (results or [])
                 if score >= 0.5 and len(text.strip()) == 1]
        if len(texts) != 1:
            raise RecognitionError('提示文字无法可靠识别')
        return texts[0]

    @staticmethod
    def _axis_box(box):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _split(box, count):
        x0, y0, x1, y1 = box
        if count == 1:
            return [box]
        parts = []
        if (x1 - x0) >= (y1 - y0):
            for index in range(count):
                parts.append((x0 + (x1 - x0) * index / count, y0,
                              x0 + (x1 - x0) * (index + 1) / count, y1))
        else:
            for index in range(count):
                parts.append((x0, y0 + (y1 - y0) * index / count,
                              x1, y0 + (y1 - y0) * (index + 1) / count))
        return parts

    def _contains(self, image, box, character):
        x0, y0, x1, y1 = (int(round(value)) for value in box)
        crop = image[max(0, y0):min(image.shape[0], y1), max(0, x0):min(image.shape[1], x1)]
        results, _ = self.engine(png_on_white(crop), use_det=False, use_cls=False)
        return any(score >= 0.5 and character in text for text, score in (results or []))

    def match(self, background, prompts):
        image = read_image(background)
        targets = [self._prompt_character(prompt) for prompt in prompts]
        if len(set(targets)) != len(targets):
            raise RecognitionError('提示文字存在重复字')
        boxes = [(box, text.strip(), score) for box, text, score in self._results(background)
                 if score >= 0.5 and text.strip()]
        if len(boxes) > 12 or sum(len(text) for _, text, _ in boxes) < len(targets):
            raise RecognitionError('文字目标检测数量异常')
        height, width = image.shape[:2]
        matches = {target: [] for target in targets}
        for box, text, _ in boxes:
            axis = self._axis_box(box)
            if not 0 <= axis[0] < axis[2] <= width or not 0 <= axis[1] < axis[3] <= height:
                raise RecognitionError('文字检测框超出图片范围')
            for character, part in zip(text, self._split(axis, len(text))):
                if character not in matches:
                    continue
                if not self._contains(image, part, character):
                    raise RecognitionError('文字定位复核不一致，不提交点击结果')
                matches[character].append(((part[0] + part[2]) / 2, (part[1] + part[3]) / 2))
        if any(len(points) != 1 for points in matches.values()):
            raise RecognitionError('文字识别结果不完整或存在歧义')
        return [matches[target][0] for target in targets], (width, height)
