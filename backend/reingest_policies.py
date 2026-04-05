from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from app.config import get_settings
from app.services.gemini import GeminiService
from app.services.supabase import get_supabase_service
from preload import process_pdf


DEFAULT_POLICY_PATHS = [
    "/Users/vaishnavibhalodi/Downloads/Medical Drug Coverage Policy Examples/UHC Botulinum Toxins A and B – Commercial Medical Benefit Drug Policy.pdf",
    "/Users/vaishnavibhalodi/Downloads/Medical Drug Coverage Policy Examples/Cigna Rituximab Intravenous Products for Non-Oncology Indications.pdf",
    "/Users/vaishnavibhalodi/Downloads/Medical Drug Coverage Policy Examples/BCBS NC - Corporate Medical Policy_ Preferred Injectable Oncology Program (Avastin example).pdf",
]


def main() -> int:
    settings = get_settings()
    gemini = GeminiService(settings)
    supabase = get_supabase_service(settings)

    paths = sys.argv[1:] or DEFAULT_POLICY_PATHS

    print("Reingesting {0} policy PDF(s) into the normalized backend schema...".format(len(paths)))
    for raw_path in paths:
        path = str(Path(raw_path).expanduser())
        if not os.path.exists(path):
            print("Missing file: {0}".format(path))
            continue
        try:
            process_pdf(path, settings, gemini, supabase)
        except Exception as exc:
            print("FAILED: {0} -> {1}".format(path, exc))

    print("\nBackend reingest run complete.")
    print("Note: this app versions documents; reingesting may create newer document versions rather than mutating old rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
