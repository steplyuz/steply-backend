"""Deterministic OpenCV OMR for the fixed Mock Center answer sheet.

No AI/LLM/OCR API is used here. The answer sheet is the fixed A4 master (ANSWER_SHEET_MASTER.pdf);
photos are rectified to the 200dpi reference raster (1654x2339) of the official master and marks are measured at
known coordinates. Free-text boxes are read only with local Tesseract when
available; uncertain text is sent to manual review instead of guessed scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

import cv2
import numpy as np

from app.modules.tests.scoring_policy import scale_listening_reading

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

# Referens kadr: rasmiy varaqning (ANSWER_SHEET_MASTER.pdf, A4) 200 dpi rasmi.
# Ilgari 1700x2200 (Letter) kadr va boshqa master'dan o'lchangan konstantalar ishlatilgan edi —
# ular rasmiy varaqqa mos kelmasdi. Hozirgi konstantalar shu master rasmidan o'lchangan.
W, H = 1654, 2339
ROOT = Path(__file__).resolve().parents[4]
MASTER_PDF = ROOT / "static" / "templates" / "ANSWER_SHEET_MASTER.pdf"
MASTER_PNG = ROOT / "static" / "templates" / "ANSWER_SHEET_MASTER_OMR.png"

# Bubble markazlari (piksel, referens kadrda). Qatorlar va harflar varaq bilan bir xil.
L1_X = [298.0, 332.0, 366.0]
L1_X2 = [466.0, 500.0, 532.0]
L1_Y = [738.5, 771.3, 804.3, 836.8]
L3_X = [298.0, 332.0, 366.0, 398.0, 432.0, 466.0]
L3_Y = [1307.3, 1340.0, 1372.6, 1407.0]
L4_X = [298.0, 332.0, 366.0, 398.0, 432.0, 466.0, 498.0, 532.0]
L4_Y = [1472.6, 1505.0, 1537.9, 1571.0, 1603.7]
L5_X1 = [298.0, 332.0, 366.0]
L5_X2 = [464.0, 498.0, 532.0]
L5_Y = [1671.0, 1702.0, 1733.2]

R2_X = [1030.0, 1064.0, 1098.0, 1130.0, 1164.0, 1196.0, 1230.0, 1264.0, 1296.0, 1330.0]
R2_Y = [1139.4, 1172.8, 1205.8, 1239.4, 1273.6, 1307.3, 1340.0, 1372.6]
R3_X = [1030.0, 1064.0, 1098.0, 1130.0, 1164.0, 1196.0, 1230.0, 1264.0]
R3_Y = [1439.0, 1472.6, 1505.0, 1537.9, 1571.0, 1603.7]
R4_X = [1028.0, 1062.0, 1096.0, 1130.0]
R4_Y = [1671.0, 1702.0, 1733.2, 1765.9]
R4_TFNG_X = [1230.0, 1264.0, 1298.0]
R4_TFNG_Y = [1671.0, 1702.0, 1733.2, 1765.9, 1799.8]
R5_X = [1028.0, 1062.0, 1096.0, 1130.0]
R5_Y = [2167.8, 2201.5]

# Erkin matn katakchalari: (savol raqamlari, qator markazlari y, x1, x2) — referens kadrda.
# Qator markazlari qizil katak chiziqlaridan (51 px qadam) hisoblangan.
TEXT_ROWS = {
    "listening_part2": (list(range(9, 15)), [981.0, 1032.0, 1083.0, 1134.0, 1185.0, 1236.0], 360, 864),
    "listening_part6": (list(range(30, 36)), [1725.5, 1776.5, 1827.5, 1878.5, 1929.5, 1980.5], 360, 864),
    "reading_part1": (list(range(1, 7)), [756.5, 807.5, 858.5, 909.5, 960.5, 1011.5], 1092, 1588),
    "reading_part5": (list(range(30, 34)), [1843.5, 1894.5, 1945.5, 1996.5], 1082, 1586),
}

@dataclass
class OMRMark:
    answer: str
    confidence: float
    darkness: dict[str, float]
    status: str


def _ensure_master_png() -> np.ndarray:
    if not MASTER_PNG.exists():
        import subprocess
        subprocess.run([
            "pdftoppm", "-f", "1", "-singlefile", "-png", "-r", "200",
            str(MASTER_PDF), str(MASTER_PNG.with_suffix(""))
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    im = cv2.imread(str(MASTER_PNG))
    if im is None:
        raise RuntimeError("OMR master rasterini ochib bo'lmadi")
    return im


def _red_mask(im: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (0, 55, 60), (12, 255, 255)) | cv2.inRange(hsv, (168, 55, 60), (179, 255, 255))


def _black_mask(im: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    # Printed red is excluded by saturation; dark pencil/pen remains.
    return ((gray < 135) & (hsv[:, :, 1] < 85)).astype(np.uint8) * 255


def _find_homography(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Find input->master homography with SIFT; reject weak/degenerate matches."""
    master = _ensure_master_png()
    src = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dst = cv2.cvtColor(master, cv2.COLOR_BGR2GRAY)
    # Downscale large camera images for stable feature extraction.
    scale = min(1.0, 1600.0 / max(src.shape[:2]))
    if scale < 1:
        small = cv2.resize(src, None, fx=scale, fy=scale)
    else:
        small = src
    sift = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.02)
    k1, d1 = sift.detectAndCompute(small, None)
    k2, d2 = sift.detectAndCompute(dst, None)
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        raise ValueError("Answer sheet uchun yetarli feature topilmadi")
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(d1, d2, k=2)
    good = [m for m, n in pairs if m.distance < 0.72 * n.distance]
    if len(good) < 12:
        raise ValueError(f"Answer sheet alignment yetarli emas: {len(good)} ta match")
    src_pts = np.float32([k1[m.queryIdx].pt for m in good]) / scale
    dst_pts = np.float32([k2[m.trainIdx].pt for m in good])
    M, inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if M is None:
        raise ValueError("Answer sheet homography topilmadi")
    inlier_ratio = float(inliers.sum()) / len(inliers) if inliers is not None else 0.0
    if inlier_ratio < 0.35:
        raise ValueError(f"Answer sheet alignment ishonchsiz: {inlier_ratio:.2f}")
    return M, inlier_ratio


def rectify(image_bytes: bytes) -> tuple[np.ndarray, float]:
    arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Rasm o'qilmadi")
    M, ratio = _find_homography(image)
    rect = cv2.warpPerspective(image, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rect, ratio


def _bubble_score(black: np.ndarray, x: float, y: float, radius: int = 13) -> float:
    xi, yi = int(round(x)), int(round(y))
    yy, xx = np.ogrid[:2*radius+1, :2*radius+1]
    disk = (xx-radius)**2 + (yy-radius)**2 <= radius*radius
    crop = black[max(0, yi-radius):yi+radius+1, max(0, xi-radius):xi+radius+1]
    if crop.shape != disk.shape:
        return 0.0
    return float((crop[disk] > 0).mean())


def _read_bubbles(black: np.ndarray, xs: list[float], ys: list[float], letters: list[str], qnums: list[int]) -> dict[str, dict[str, Any]]:
    out = {}
    for q, y in zip(qnums, ys):
        vals = {letter: _bubble_score(black, x, y) for letter, x in zip(letters, xs)}
        ordered = sorted(vals.items(), key=lambda kv: kv[1], reverse=True)
        best, best_score = ordered[0]
        second_score = ordered[1][1] if len(ordered) > 1 else 0.0
        # Ikki yoki undan ko'p bubble belgi chegarasidan o'tsa, javob aniq emas (ikki marta belgilangan):
        # eng qorasini tanlab "MARKED" qilmaymiz — qo'lda tekshiruvga yuboriladi.
        marked_letters = [letter for letter, value in vals.items() if value >= 0.34]
        # Blank template bubbles have very low interior darkness; printed letters are mostly outside the inner disk.
        if best_score < 0.12:
            answer, status = "", "BLANK"
        elif len(marked_letters) > 1:
            answer, status = "?", "AMBIGUOUS"
        elif best_score >= 0.34 and (best_score - second_score >= 0.07 or best_score >= 0.52):
            answer, status = best, "MARKED"
        elif best_score >= 0.20:
            answer, status = "?", "AMBIGUOUS"
        else:
            answer, status = "", "BLANK"
        conf = max(0.0, min(1.0, (best_score - second_score) / 0.25)) if answer not in ("", "?") else 0.0
        out[str(q)] = {"answer": answer, "status": status, "confidence": round(conf, 3), "darkness": {k: round(v, 4) for k, v in vals.items()}}
    return out


def _crop_ink(img: np.ndarray, y: int, x1: int, x2: int) -> tuple[np.ndarray, float]:
    crop = img[max(0, y-20):min(H, y+21), x1:x2]
    red = _red_mask(crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray[red > 0] = 255
    return gray, float((gray < 120).mean())


_BASELINE_CACHE: dict[tuple[int, int, int], float] = {}


def _extra_ink(rect: np.ndarray, y: int, x1: int, x2: int) -> float:
    """Katakchadagi yozuv ulushi, master (bo'sh varaq) ink'idan ortiqcha qismi.

    Bosma tasvir va soyalar bo'sh varaqda ham ink beradi; ularni "yozuv" deb hisoblamaymiz.
    """
    key = (y, x1, x2)
    if key not in _BASELINE_CACHE:
        _BASELINE_CACHE[key] = _crop_ink(_ensure_master_png(), y, x1, x2)[1]
    return max(0.0, _crop_ink(rect, y, x1, x2)[1] - _BASELINE_CACHE[key])


def _ocr_text_row(rect: np.ndarray, y: int, x1: int, x2: int) -> tuple[str, float]:
    gray, _ = _crop_ink(rect, y, x1, x2)
    if _extra_ink(rect, y, x1, x2) < 0.003:
        return "", 0.0
    bw = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1]
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 9))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_h)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_v)
    bw = cv2.bitwise_not(bw)
    if pytesseract is None:
        # OCR yo'q: yozuv bor-yo'qligini bilamiz, lekin o'qiy olmaymiz — jim "bo'sh" emas,
        # qo'lda tekshiruvga yuboriladi (conf = -1 belgisi).
        return "", -1.0
    cfg = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-/."
    data = pytesseract.image_to_data(bw, config=cfg, output_type=pytesseract.Output.DICT)
    texts, confs = [], []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        t = re.sub(r"[^A-Za-z0-9:/.'-]", "", text or "")
        if t and float(conf) >= 0:
            texts.append(t); confs.append(float(conf)/100)
    return "".join(texts).upper(), (sum(confs)/len(confs) if confs else 0.0)


def _read_text_rows(rect: np.ndarray, key: str) -> dict[str, dict[str, Any]]:
    qnums, ys, x1, x2 = TEXT_ROWS[key]
    result = {}
    for q, y in zip(qnums, ys):
        text, conf = _ocr_text_row(rect, int(round(y)), x1, x2)
        if conf < 0:  # OCR mavjud emas, lekin katakchada yozuv bor
            result[str(q)] = {"answer": "?", "status": "AMBIGUOUS", "confidence": 0.0, "darkness": {}}
            continue
        status = "MARKED" if text else "BLANK"
        if text and conf < 0.55:
            status = "AMBIGUOUS"
        if not text and _extra_ink(rect, int(round(y)), x1, x2) >= 0.003:
            # Katakchada yozuv bor, lekin o'qilmadi: jim "bo'sh" qilmaymiz.
            status = "AMBIGUOUS"
        result[str(q)] = {"answer": text, "status": status, "confidence": round(conf, 3)}
    return result


def read_sheet(image_bytes: bytes, candidate_id: str | None = None) -> dict[str, Any]:
    rect, alignment = rectify(image_bytes)
    black = _black_mask(rect)
    listening: dict[str, Any] = {}
    reading: dict[str, Any] = {}

    # Listening Part 1: 1-4 left, 5-8 right.
    listening.update(_read_bubbles(black, L1_X, L1_Y, ["A", "B", "C"], [1,2,3,4]))
    listening.update(_read_bubbles(black, L1_X2, L1_Y, ["A", "B", "C"], [5,6,7,8]))
    listening.update(_read_text_rows(rect, "listening_part2"))
    listening.update(_read_bubbles(black, L3_X, L3_Y, list("ABCDEF"), [15,16,17,18]))
    listening.update(_read_bubbles(black, L4_X, L4_Y, list("ABCDEFGH"), [19,20,21,22,23]))
    listening.update(_read_bubbles(black, L5_X1, L5_Y, list("ABC"), [24,25,26]))
    listening.update(_read_bubbles(black, L5_X2, L5_Y, list("ABC"), [27,28,29]))
    listening.update(_read_text_rows(rect, "listening_part6"))

    # Reading
    reading.update(_read_text_rows(rect, "reading_part1"))
    reading.update(_read_bubbles(black, R2_X, R2_Y, list("ABCDEFGHIJ"), [7,8,9,10,11,12,13,14]))
    reading.update(_read_bubbles(black, R3_X, R3_Y, list("ABCDEFGH"), [15,16,17,18,19,20]))
    reading.update(_read_bubbles(black, R4_X, R4_Y, list("ABCD"), [21,22,23,24]))
    reading.update(_read_bubbles(black, R4_TFNG_X, R4_TFNG_Y, ["A","B","C"], [25,26,27,28,29]))
    reading.update(_read_text_rows(rect, "reading_part5"))
    reading.update(_read_bubbles(black, R5_X, R5_Y, list("ABCD"), [34,35]))

    # Ikki bo'lim ham 1..35 raqamlanadi: ularni birlashtirmasdan, har birini alohida hisoblaymiz.
    ambiguous_listening = sorted(int(q) for q, v in listening.items() if v.get("status") == "AMBIGUOUS")
    ambiguous_reading = sorted(int(q) for q, v in reading.items() if v.get("status") == "AMBIGUOUS")
    ambiguous = ambiguous_listening + ambiguous_reading
    marked_count = sum(1 for v in list(listening.values()) + list(reading.values()) if v.get("answer"))
    manual = len(ambiguous) > 0 or not candidate_id or alignment < 0.55
    confidence = "high" if alignment >= 0.7 and not ambiguous else ("medium" if alignment >= 0.55 else "low")
    return {
        "candidate_id": candidate_id or "UNKNOWN",
        "listening": {k:v["answer"] for k,v in sorted(listening.items(), key=lambda x:int(x[0]))},
        "reading": {k:v["answer"] for k,v in sorted(reading.items(), key=lambda x:int(x[0]))},
        "details": {"listening": listening, "reading": reading},
        "alignment_inlier_ratio": round(alignment, 4),
        "ambiguous_questions": sorted(set(ambiguous)),
        "ambiguous_listening": ambiguous_listening,
        "ambiguous_reading": ambiguous_reading,
        "marked_count": marked_count,
        "confidence": confidence,
        "manual_review": manual,
        "notes": "Candidate ID sheetda bosilmagan; ID request/filename orqali berilishi kerak." if not candidate_id else "",
    }


def normalize_answer(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def compare_answers(student: dict[str, str], key: dict[str, Any]) -> dict[str, Any]:
    correct, wrong, blank, ambiguous = [], [], [], []
    for q, expected in key.items():
        qn = int(q)
        got = normalize_answer(student.get(str(q), ""))
        if got == "?":
            ambiguous.append(qn); continue
        accepted = expected if isinstance(expected, list) else str(expected).split("/")
        accepted = {normalize_answer(x) for x in accepted if normalize_answer(x)}
        if not got:
            blank.append(qn)
        elif got in accepted:
            correct.append(qn)
        else:
            wrong.append(qn)
    return {"raw_score": len(correct), "total": len(key), "correct_questions": sorted(correct), "wrong_questions": sorted(wrong), "blank_questions": sorted(blank), "ambiguous_questions": sorted(ambiguous)}


def scale_score(raw: int, total: int = 35) -> tuple[float, str]:
    return scale_listening_reading(raw, total)
