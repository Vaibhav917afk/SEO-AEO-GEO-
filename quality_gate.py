import json
import sys

from backend.config import load_settings
from backend.quality import QualityRunner


def main() -> int:
    report = QualityRunner(load_settings()).run()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
