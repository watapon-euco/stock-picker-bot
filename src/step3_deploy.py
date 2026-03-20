"""
Step 3: GitHub Pages デプロイ

生成された docs/ と data/theme_history.json を git commit して push する。
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """サブプロセスを実行してログ出力する"""
    logger.debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        logger.debug(result.stdout.strip())
    if result.stderr:
        logger.debug(result.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def run():
    # git設定（GitHub Actions では既に設定済みのことが多いが念のため）
    git_name = os.environ.get("GIT_AUTHOR_NAME", "github-actions[bot]")
    git_email = os.environ.get(
        "GIT_AUTHOR_EMAIL", "github-actions[bot]@users.noreply.github.com"
    )
    _run(["git", "config", "user.name", git_name])
    _run(["git", "config", "user.email", git_email])

    # ステージング
    paths_to_add = ["docs/", "data/theme_history.json"]
    for p in paths_to_add:
        if Path(p).exists():
            _run(["git", "add", p])
        else:
            logger.warning(f"Path does not exist, skipping: {p}")

    # コミットメッセージ
    now = datetime.now(timezone.utc).astimezone()
    year_month = now.strftime("%Y-%m")
    commit_msg = f"📊 Auto-generate monthly report {year_month}"

    # コミット（変更がなければ exit 1 → 正常終了として扱う）
    result = _run(
        ["git", "commit", "-m", commit_msg],
        check=False,
    )
    if result.returncode == 0:
        logger.info(f"Committed: {commit_msg}")
    elif "nothing to commit" in result.stdout + result.stderr:
        logger.info("Nothing to commit. Skipping push.")
        return
    else:
        raise RuntimeError(
            f"git commit failed (exit {result.returncode}):\n{result.stderr}"
        )

    # 現在のブランチを取得してプッシュ
    branch_result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_result.stdout.strip()

    # ブランチ名バリデーション（フラグ注入防止）
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9/_.\-]+$', branch):
        raise ValueError(f"Unexpected branch name format: {branch!r}")

    logger.info(f"Pushing to origin/{branch}...")

    _run(["git", "push", "-u", "origin", branch])
    logger.info("Step 3 complete: pushed to GitHub Pages.")


if __name__ == "__main__":
    run()
