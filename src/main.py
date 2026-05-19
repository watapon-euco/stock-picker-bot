"""
パイプライン全体のオーケストレーター

使用方法:
  python -m src.main              # 全ステップ実行
  python -m src.main --step 1     # Step 1 のみ実行
  python -m src.main --step 1 2   # Step 1, 2 のみ実行
"""
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="テーマ株レポート自動生成パイプライン")
    parser.add_argument(
        "--step",
        nargs="*",
        type=int,
        choices=[1, 2, 3, 4, 5, 6],
        # Step 5 (watchlist) と Step 6 (backtest dashboard) はデフォルト実行に含めない。
        # 月次メインフローは 1-4 のまま。--step 6 で明示的に実行すること。
        help="実行するステップ番号（省略時は Step 1-4 を実行）",
    )
    args = parser.parse_args()

    # Step 5（ウォッチリスト）は月次メインパイプラインとは独立した補助機能のため、
    # デフォルト全ステップ実行には含めない。明示的に --step 5 を指定して使用すること。
    steps_to_run = set(args.step) if args.step else {1, 2, 3, 4}

    # Step 1: ニュース収集 + テーマ抽出 + 銘柄調査 + 株価取得
    if 1 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 1: Research (News → Themes → Stocks → yfinance)")
        logger.info("=" * 60)
        from src import step1_research
        step1_research.run()

    # Step 2: AI 分析 + HTML レポート生成
    if 2 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 2: Report Generation (Claude Batch API)")
        logger.info("=" * 60)
        from src import step2_report
        step2_report.run()

    # Step 3: GitHub Pages デプロイ
    if 3 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 3: Deploy to GitHub Pages")
        logger.info("=" * 60)
        from src import step3_deploy
        step3_deploy.run()

    # Step 4: LINE 通知（失敗してもパイプライン全体はブロックしない）
    if 4 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 4: LINE Notification")
        logger.info("=" * 60)
        try:
            from src import step4_notify
            step4_notify.run()
        except Exception as e:
            logger.warning(f"Step 4 failed (non-blocking): {e}")

    # Step 5: ウォッチリスト監視（失敗してもパイプライン全体はブロックしない）
    if 5 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 5: Watchlist Check")
        logger.info("=" * 60)
        try:
            from src import step5_watchlist
            step5_watchlist.run()
        except Exception as e:
            logger.warning(f"Step 5 failed (non-blocking): {e}")

    # Step 6: バックテスト成績ダッシュボード生成（失敗してもパイプライン全体はブロックしない）
    if 6 in steps_to_run:
        logger.info("=" * 60)
        logger.info("STEP 6: Backtest Performance Dashboard")
        logger.info("=" * 60)
        try:
            from src import step6_backtest
            step6_backtest.run()
        except Exception as e:
            logger.warning(f"Step 6 failed (non-blocking): {e}")

    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()
