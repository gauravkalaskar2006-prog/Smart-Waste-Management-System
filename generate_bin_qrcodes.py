from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import qrcode


def sanitize_filename(bin_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in bin_id)


def build_base_url(server_ip: str, port: int) -> str:
    return f"http://{server_ip}:{port}"


def generate_qr_codes(db_path: Path, output_dir: Path, base_url: str) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT bin_id FROM bins ORDER BY bin_id").fetchall()
    finally:
        conn.close()

    generated = 0
    for (bin_id,) in rows:
        if not bin_id:
            continue
        qr_payload = f"{base_url}/complaint/{bin_id}"
        filename = sanitize_filename(bin_id)
        out_file = output_dir / f"{filename}.png"

        img = qrcode.make(qr_payload)
        img.save(out_file)
        generated += 1
        print(f"Generated: {out_file} -> {qr_payload}")

    return generated


def main() -> None:
    root = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Generate QR codes for bins from SQLite DB.")
    parser.add_argument("--server-ip", default="127.0.0.1", help="Server IP used inside QR payload URL")
    parser.add_argument("--port", type=int, default=5000, help="Server port used inside QR payload URL")
    parser.add_argument("--db", default="database.db", help="SQLite database path (default: database.db)")
    parser.add_argument(
        "--output",
        default="static/qrcodes",
        help="Output directory for QR images (default: static/qrcodes)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = root / db_path

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    base_url = build_base_url(args.server_ip, args.port)
    total = generate_qr_codes(db_path=db_path, output_dir=output_dir, base_url=base_url)
    print(f"Done. Generated {total} QR code(s) in {output_dir}")


if __name__ == "__main__":
    main()
