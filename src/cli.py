"""命令入口：校验事件文件、审计段落、查询有效表述、恢复续办。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .envelope import validate_event
from .registry import Registry

_COMMANDS = ("check", "audit", "effective", "resume")


def _check_file(event_file: str) -> int:
    try:
        record = json.loads(Path(event_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"无法读取事件：{error}", file=sys.stderr)
        return 2
    errors = validate_event(record)
    if errors:
        print("；".join(errors), file=sys.stderr)
        return 1
    print(f"事件有效：{record['event_id']}")
    return 0


def _print_audit(store_path: str, passage_id: str) -> int:
    registry = Registry(store_path)
    try:
        report = registry.query.audit_passage(passage_id)
    except ValueError as error:
        print(f"无法审计：{error}", file=sys.stderr)
        return 1
    print(f"段落 {report['passage_id']}（{report['carrier']}/{report['audience']}）状态 {report['state']}，版本 {report['version']}")
    print(f"当前表述：{report['text']}")
    print("术语追溯：")
    for item in report["terms"]:
        print(f"- {item['term_id']}")
        print(f"  原文词形：{item['original_form']}　转写：{item['transliteration']}")
        print(f"  语义范围：{item['semantic_range']}")
        print(f"  出处：{item['provenance']}　年代语境：{item['dating_context']}")
        if item["forbidden"]:
            print(f"  禁用误译：{'、'.join(item['forbidden'])}")
        candidate = item["candidate"]
        if candidate:
            print(f"  中文候选：{candidate['text']}（{candidate['audience']}，{candidate['proposed_by']} 提出，依据 {candidate['source_ref'] or '未注明'}）")
        for review in item["reviews"]:
            print(f"  审定 {review['request_id']}：{review['status']}（{review['decided_at'] or '待定'}）")
            for side, key in (("埃及方", "egypt_review"), ("中方", "curator_review")):
                opinion = review.get(key)
                if opinion:
                    decision = "确认" if opinion["approve"] else "驳回"
                    print(f"    {side} {opinion['reviewer']}：{opinion['opinion']}（{decision}）")
    if report["errata"]:
        print("勘误记录：")
        for errata in report["errata"]:
            until = errata["valid_until"] or "无限期"
            changes = "；".join(f"{change['from_text']}→{change['to_text']}" for change in errata["changes"])
            signers = "、".join(signature["signer"] for signature in errata["signatures"])
            print(f"- {errata['errata_id']} 适用期 {errata['valid_from']} 至 {until}：{changes}（签署：{signers}）")
    if report["revisions"]:
        print(f"历次修订：{'、'.join(report['revisions'])}")
    return 0


def _print_effective(store_path: str, passage_id: str, on_date: str) -> int:
    registry = Registry(store_path)
    try:
        result = registry.query.effective_passage(passage_id, on_date)
    except ValueError as error:
        print(f"无法查询：{error}", file=sys.stderr)
        return 1
    print(result["text"])
    return 0


def _resume(store_path: str) -> int:
    registry = Registry(store_path)
    report = registry.recover()
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) == 1 and argv[0] not in _COMMANDS:
        return _check_file(argv[0])  # 兼容旧用法：python3 -m src.cli <事件文件>
    parser = argparse.ArgumentParser(prog="src.cli", description="联合考古上下文版本库命令入口")
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_check = subparsers.add_parser("check", help="校验事件文件")
    p_check.add_argument("event_file")
    p_audit = subparsers.add_parser("audit", help="从中文段落追溯原文、证据与历次取舍")
    p_audit.add_argument("store")
    p_audit.add_argument("passage_id")
    p_effective = subparsers.add_parser("effective", help="按发布日期给出当时有效表述")
    p_effective.add_argument("store")
    p_effective.add_argument("passage_id")
    p_effective.add_argument("--date", required=True, help="ISO 8601 日期时间")
    p_resume = subparsers.add_parser("resume", help="服务恢复：继续待会签修订")
    p_resume.add_argument("store")
    args = parser.parse_args(argv)
    if args.command == "check":
        return _check_file(args.event_file)
    if args.command == "audit":
        return _print_audit(args.store, args.passage_id)
    if args.command == "effective":
        return _print_effective(args.store, args.passage_id, args.date)
    if args.command == "resume":
        return _resume(args.store)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
