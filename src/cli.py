#!/usr/bin/env python3
import sys
import json
import argparse
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.utils.logger import setup_logger
from src.pipeline import GovernancePipeline

logger = setup_logger()


def cmd_run(args):
    pipeline = GovernancePipeline()
    try:
        result = pipeline.run(source=args.source)
        print(json.dumps(result["card"], ensure_ascii=False, indent=2))
        if args.stats:
            print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    finally:
        pipeline.close()


def cmd_refresh(args):
    pipeline = GovernancePipeline()
    try:
        result = pipeline.refresh_all()
        print(json.dumps(result["card"], ensure_ascii=False, indent=2))
    finally:
        pipeline.close()


def cmd_fetch(args):
    pipeline = GovernancePipeline()
    try:
        result = pipeline.fetch(urls=args.urls)
        print(json.dumps(result["card"], ensure_ascii=False, indent=2))
        if args.stats:
            print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    finally:
        pipeline.close()


def cmd_permissions(args):
    pipeline = GovernancePipeline()
    try:
        result = pipeline.govern_permissions()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        pipeline.close()


def cmd_stats(args):
    from src.utils.config import load_config
    from src.utils.db import GovernanceDB
    config = load_config()
    db = GovernanceDB(config.get("db", {}).get("path", "./data/governance.db"))
    try:
        print(json.dumps(db.get_stats(), ensure_ascii=False, indent=2))
    finally:
        db.close()


def cmd_init(args):
    config_example = SKILL_DIR / "config" / "config.example.yaml"
    config_target = SKILL_DIR / "config.yaml"
    if config_target.exists() and not args.force:
        print(f"config.yaml 已存在，使用 --force 覆盖")
        return
    import shutil
    shutil.copy2(config_example, config_target)
    print(f"已创建配置文件: {config_target}")
    print("请编辑 config.yaml 填入飞书 Bitable 配置")


def main():
    parser = argparse.ArgumentParser(
        prog="file-governance",
        description="企业级文件与数据治理 Skill - 飞书知识沉淀"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="增量扫描并治理新文件")
    p_run.add_argument("--source", default="all", choices=["all", "wechat", "inbox", "local_folder", "url_fetch"])
    p_run.add_argument("--stats", action="store_true", help="输出统计数据")
    p_run.set_defaults(func=cmd_run)

    p_fetch = sub.add_parser("fetch", help="按文件地址自动爬取并治理沉淀（支持 URL 或本地路径）")
    p_fetch.add_argument("urls", nargs="+", help="一个或多个文件地址（http/https URL 或本地绝对路径）")
    p_fetch.add_argument("--stats", action="store_true", help="输出统计数据")
    p_fetch.set_defaults(func=cmd_fetch)

    p_refresh = sub.add_parser("refresh", help="全量刷新：清空 Bitable 和本地状态后重跑")
    p_refresh.set_defaults(func=cmd_refresh)

    p_perm = sub.add_parser("govern-permissions", help="批量治理文件权限（密级+tenant_readable）")
    p_perm.set_defaults(func=cmd_permissions)

    p_stats = sub.add_parser("stats", help="显示治理统计")
    p_stats.set_defaults(func=cmd_stats)

    p_init = sub.add_parser("init", help="初始化配置文件")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
