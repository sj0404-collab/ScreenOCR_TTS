"""
config_export.py — Экспорт/импорт настроек.

Использование:
    python config_export.py export backup.json
    python config_export.py import backup.json
    python config_export.py diff current.json backup.json
"""
import json
import os
import shutil
import time
from pathlib import Path


class ConfigExporter:
    """Экспорт/импорт конфигурации."""
    def __init__(self, config_dir: str = None):
        if config_dir is None:
            config_dir = os.path.dirname(__file__)
        self.config_dir = Path(config_dir)
        self.config_files = [
            "config.json",
            "hotkeys.json",
            "theme.json",
            "voice_profiles.json",
        ]

    def export_all(self, output_path: str) -> str:
        """Экспорт всех настроек в один JSON."""
        data = {
            "exported_at": time.time(),
            "exported_time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "configs": {},
        }

        for filename in self.config_files:
            filepath = self.config_dir / filename
            if filepath.exists():
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data["configs"][filename] = json.load(f)
                except Exception:
                    data["configs"][filename] = None

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return output_path

    def import_all(self, input_path: str, overwrite: bool = True) -> list[str]:
        """Импорт настроек из JSON."""
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        imported = []
        configs = data.get("configs", {})

        for filename, content in configs.items():
            if filename not in self.config_files:
                continue
            if content is None:
                continue

            filepath = self.config_dir / filename
            if filepath.exists() and not overwrite:
                # Merge instead of overwrite
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    merged = self._merge_dicts(existing, content)
                    content = merged
                except Exception:
                    pass

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(content, f, ensure_ascii=False, indent=2)
            imported.append(filename)

        return imported

    def _merge_dicts(self, base: dict, override: dict) -> dict:
        """Рекурсивный merge."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            else:
                result[key] = value
        return result

    def diff(self, path_a: str, path_b: str) -> dict:
        """Сравнение двух конфигов."""
        with open(path_a, "r", encoding="utf-8") as f:
            a = json.load(f)
        with open(path_b, "r", encoding="utf-8") as f:
            b = json.load(f)

        diffs = {}
        self._diff_dicts(a, b, "", diffs)
        return diffs

    def _diff_dicts(self, a: dict, b: dict, prefix: str, diffs: dict):
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            full_key = f"{prefix}.{key}" if prefix else key
            if key not in a:
                diffs[full_key] = {"status": "added", "value": b[key]}
            elif key not in b:
                diffs[full_key] = {"status": "removed", "value": a[key]}
            elif isinstance(a[key], dict) and isinstance(b[key], dict):
                self._diff_dicts(a[key], b[key], full_key, diffs)
            elif a[key] != b[key]:
                diffs[full_key] = {"status": "changed", "old": a[key], "new": b[key]}
        return diffs

    def backup(self, backup_dir: str = None) -> str:
        """Бэкап всех конфиг-файлов в папку."""
        if backup_dir is None:
            backup_dir = str(self.config_dir / "backups" / time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(backup_dir, exist_ok=True)

        for filename in self.config_files:
            src = self.config_dir / filename
            if src.exists():
                dst = Path(backup_dir) / filename
                shutil.copy2(str(src), str(dst))

        return backup_dir


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Config export/import")
    sub = parser.add_subparsers(dest="action")

    p_export = sub.add_parser("export", help="Export configs")
    p_export.add_argument("output", help="Output JSON file")

    p_import = sub.add_parser("import", help="Import configs")
    p_import.add_argument("input", help="Input JSON file")
    p_import.add_argument("--no-overwrite", action="store_true")

    p_diff = sub.add_parser("diff", help="Diff two configs")
    p_diff.add_argument("a")
    p_diff.add_argument("b")

    p_backup = sub.add_parser("backup", help="Backup configs")
    p_backup.add_argument("--dir", default=None)

    args = parser.parse_args()
    ce = ConfigExporter()

    if args.action == "export":
        path = ce.export_all(args.output)
        print(f"Exported: {path}")
    elif args.action == "import":
        files = ce.import_all(args.input, not args.no_overwrite)
        print(f"Imported: {', '.join(files)}")
    elif args.action == "diff":
        diffs = ce.diff(args.a, args.b)
        for key, info in diffs.items():
            if info["status"] == "changed":
                print(f"  CHANGED: {key}: {info['old']} -> {info['new']}")
            elif info["status"] == "added":
                print(f"  ADDED: {key}: {info['value']}")
            elif info["status"] == "removed":
                print(f"  REMOVED: {key}: {info['value']}")
        if not diffs:
            print("No differences")
    elif args.action == "backup":
        path = ce.backup(args.dir)
        print(f"Backup: {path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
