"""
batch_ocr.py — Пакетная обработка изображений.

Использование:
    python batch_ocr.py folder/ --langs ru+en
    python batch_ocr.py *.png --output results.json
"""
import os
import json
import time
from pathlib import Path
from typing import Optional, Callable

try:
    from PIL import Image
except ImportError:
    Image = None


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}


class BatchOCR:
    """Пакетное OCR-распознавание."""
    def __init__(self, ocr_func=None, preprocessor=None):
        self.ocr_func = ocr_func
        self.preprocessor = preprocessor
        self.results = []

    def scan_folder(self, folder: str, langs: str = "ru+en",
                    recursive: bool = True,
                    progress_callback: Callable = None) -> list[dict]:
        """Сканирование папки с изображениями."""
        folder = Path(folder)
        if not folder.exists():
            return []

        files = []
        if recursive:
            for ext in IMAGE_EXTS:
                files.extend(folder.rglob(f"*{ext}"))
                files.extend(folder.rglob(f"*{ext.upper()}"))
        else:
            for ext in IMAGE_EXTS:
                files.extend(folder.glob(f"*{ext}"))
                files.extend(folder.glob(f"*{ext.upper()}"))

        files = sorted(set(files))
        self.results = []

        for i, filepath in enumerate(files):
            if progress_callback:
                progress_callback(i + 1, len(files), str(filepath.name))

            result = self.process_file(str(filepath), langs)
            self.results.append(result)

        return self.results

    def process_file(self, filepath: str, langs: str = "ru+en") -> dict:
        """Обработка одного файла."""
        result = {
            "file": filepath,
            "filename": os.path.basename(filepath),
            "timestamp": time.time(),
            "text": "",
            "error": None,
            "success": False,
        }

        try:
            if Image is None:
                raise ImportError("Pillow not installed")

            img = Image.open(filepath)

            if self.preprocessor:
                try:
                    from image_preprocessor import preprocess_for_ocr
                    img = preprocess_for_ocr(img)
                except Exception:
                    pass

            if self.ocr_func:
                import numpy as np
                arr = np.array(img)
                text = self.ocr_func(arr, langs)
            else:
                text = "[OCR not configured]"

            result["text"] = text.strip()
            result["success"] = True
            result["chars"] = len(text)
            result["lines"] = len(text.split("\n")) if text else 0

        except Exception as e:
            result["error"] = str(e)

        return result

    def process_images(self, image_paths: list, langs: str = "ru+en",
                       progress_callback: Callable = None) -> list[dict]:
        """Обработка списка изображений."""
        self.results = []
        for i, path in enumerate(image_paths):
            if progress_callback:
                progress_callback(i + 1, len(image_paths), os.path.basename(path))
            result = self.process_file(path, langs)
            self.results.append(result)
        return self.results

    def save_results(self, output_path: str, format: str = "json"):
        """Сохранение результатов."""
        if format == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
        elif format == "txt":
            with open(output_path, "w", encoding="utf-8") as f:
                for r in self.results:
                    f.write(f"=== {r['filename']} ===\n")
                    f.write(r.get("text", "") + "\n\n")
        elif format == "csv":
            import csv
            with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["filename", "text", "chars", "lines", "error"])
                writer.writeheader()
                for r in self.results:
                    writer.writerow({
                        "filename": r["filename"],
                        "text": r.get("text", ""),
                        "chars": r.get("chars", 0),
                        "lines": r.get("lines", 0),
                        "error": r.get("error", ""),
                    })

    def get_summary(self) -> dict:
        """Итоговая статистика."""
        total = len(self.results)
        success = sum(1 for r in self.results if r["success"])
        failed = total - success
        total_chars = sum(r.get("chars", 0) for r in self.results)
        total_lines = sum(r.get("lines", 0) for r in self.results)
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "total_chars": total_chars,
            "total_lines": total_lines,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch OCR")
    parser.add_argument("input", help="Folder or files")
    parser.add_argument("--langs", default="ru+en")
    parser.add_argument("--output", "-o", default="batch_results.json")
    parser.add_argument("--format", choices=["json", "txt", "csv"], default="json")
    parser.add_argument("--no-recursive", action="store_true")
    args = parser.parse_args()

    batch = BatchOCR()

    input_path = Path(args.input)
    if input_path.is_dir():
        results = batch.scan_folder(str(input_path), args.langs, not args.no_recursive,
                                     progress_callback=lambda i, n, name: print(f"  [{i}/{n}] {name}"))
    else:
        files = [str(input_path)]
        results = batch.process_images(files, args.langs)

    batch.save_results(args.output, args.format)
    summary = batch.get_summary()
    print(f"\nDone: {summary['success']}/{summary['total']} files, "
          f"{summary['total_chars']} chars, {summary['total_lines']} lines")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
