"""Create a local ``.env`` file without exposing the database password.

This script only uses Python's standard library so it can run immediately after
the virtual environment is created. The PostgreSQL password is read with
``getpass`` and is never printed or passed on the command line.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import quote


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = BACKEND_DIR / ".env.example"
ENV_PATH = BACKEND_DIR / ".env"


def configure_console_encoding() -> None:
    """Keep Vietnamese prompts usable in older Windows PowerShell consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Tạo BE/.env an toàn: hỏi mật khẩu PostgreSQL ở chế độ ẩn, "
            "URL encode mật khẩu và tự sinh JWT secret."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="ghi đè BE/.env hiện có mà không hỏi lại",
    )
    parser.add_argument("--database-user", default="eldercare_app")
    parser.add_argument("--database-name", default="eldercare_dev")
    parser.add_argument("--database-host", default="localhost")
    parser.add_argument("--database-port", type=int, default=5432)
    return parser


def replace_setting(template: str, key: str, value: str) -> str:
    prefix = f"{key}="
    lines = template.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            break
    else:
        lines.append(f"{prefix}{value}")
    return "\n".join(lines) + "\n"


def user_allows_overwrite(force: bool) -> bool:
    if not ENV_PATH.exists() or force:
        return True

    print(f"File đã tồn tại: {ENV_PATH}")
    answer = input("Gõ GHI_DE để thay thế file này, hoặc nhấn Enter để hủy: ").strip()
    return answer == "GHI_DE"


def main() -> int:
    configure_console_encoding()
    args = build_parser().parse_args()

    if not 1 <= args.database_port <= 65535:
        print("Lỗi: database port phải nằm trong khoảng 1 đến 65535.", file=sys.stderr)
        return 2

    if not ENV_EXAMPLE_PATH.is_file():
        print(f"Lỗi: không tìm thấy {ENV_EXAMPLE_PATH}", file=sys.stderr)
        return 2

    if not user_allows_overwrite(args.force):
        print("Đã hủy. BE/.env hiện có không bị thay đổi.")
        return 0

    password = getpass(
        f"Nhập mật khẩu PostgreSQL của role {args.database_user}: "
    )
    if not password:
        print("Lỗi: mật khẩu không được để trống.", file=sys.stderr)
        return 2

    confirmation = getpass("Nhập lại mật khẩu PostgreSQL: ")
    if password != confirmation:
        print("Lỗi: hai lần nhập mật khẩu không khớp.", file=sys.stderr)
        return 2

    encoded_user = quote(args.database_user, safe="")
    encoded_password = quote(password, safe="")
    encoded_database = quote(args.database_name, safe="")
    database_url = (
        "postgresql+asyncpg://"
        f"{encoded_user}:{encoded_password}@{args.database_host}:"
        f"{args.database_port}/{encoded_database}"
    )

    content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    content = replace_setting(content, "DATABASE_URL", database_url)
    content = replace_setting(content, "JWT_SECRET", secrets.token_urlsafe(48))

    temporary_path = ENV_PATH.with_name(f"{ENV_PATH.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8", newline="\n")
    temporary_path.replace(ENV_PATH)

    print(f"Đã tạo an toàn: {ENV_PATH}")
    print("Mật khẩu không được hiển thị và JWT secret đã được sinh tự động.")
    print("Tiếp theo chạy: python -m alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
