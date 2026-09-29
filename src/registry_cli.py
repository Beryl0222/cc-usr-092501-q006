"""双语术语与叙事版本库的编辑与审计命令入口。

用法示例：

    python3 -m src.registry_cli --log data/registry.jsonl register-term \
        --term-id TERM-RA --lemma "rꜥ" --transliteration "Ra" \
        --semantic-range "太阳神名，亦指太阳本身" \
        --provenance "《亡灵书》第15章" --dating-context "新王国时期" \
        --forbid "拉神灯=混淆神名与器物" --by translator:li
"""

from __future__ import annotations

import argparse
import json
import sys

from .registry import AUDIENCES, CARRIERS, REVIEW_ROLES, VERDICTS, Registry, RegistryError


def _pairs(values: list[str]) -> dict:
    result = {}
    for item in values or []:
        if "=" not in item:
            raise RegistryError(f"键值对须形如 名称=内容：{item}")
        key, _, value = item.partition("=")
        result[key.strip()] = value.strip()
    return result


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="registry", description="双语术语与叙事版本库")
    parser.add_argument("--log", default="data/registry.jsonl", help="事件日志路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register-term", help="登记源语术语")
    p.add_argument("--term-id", required=True)
    p.add_argument("--lemma", required=True, help="原文词形")
    p.add_argument("--transliteration", required=True, help="转写")
    p.add_argument("--semantic-range", required=True, help="语义范围")
    p.add_argument("--provenance", required=True, help="出处")
    p.add_argument("--dating-context", required=True, help="年代语境")
    p.add_argument("--forbid", action="append", default=[], metavar="误译=原因", help="禁用误译")
    p.add_argument("--by", required=True, help="登记人")

    p = sub.add_parser("propose", help="译者提出中文候选")
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--term-id", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--audience", required=True, choices=AUDIENCES)
    p.add_argument("--by", required=True, help="提案人")
    p.add_argument("--note", default="")

    p = sub.add_parser("review-open", help="发起审定请求（重复到达返回既有决定）")
    p.add_argument("--request-id", required=True)
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--by", required=True, help="提交人")
    p.add_argument("--provenance", default=None, help="请求所依据的出处，缺省取术语当前出处")
    p.add_argument("--urgent", action="store_true", help="紧急纠错（仍需双方签署）")

    p = sub.add_parser("review-opinion", help="签署一方审定意见")
    p.add_argument("--request-id", required=True)
    p.add_argument("--role", required=True, choices=REVIEW_ROLES)
    p.add_argument("--by", required=True, help="签署人")
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--rationale", required=True, help="责任签署说明")

    p = sub.add_parser("passage-register", help="登记叙事段落（展签/图录/讲稿/课程）")
    p.add_argument("--passage-id", required=True)
    p.add_argument("--carrier", required=True, choices=CARRIERS)
    p.add_argument("--audience", required=True, choices=AUDIENCES)
    p.add_argument("--text", required=True, help="段落中文文本")
    p.add_argument("--use", action="append", default=[], metavar="术语=表述", help="段落引用的术语表述")

    p = sub.add_parser("passage-publish", help="标记段落已印刷或讲授")
    p.add_argument("--passage-id", required=True)

    p = sub.add_parser("revise", help="术语修订：未发布迁移，已发布追加勘误")
    p.add_argument("--term", action="append", required=True, help="待修订术语，可多次")
    p.add_argument("--audience", required=True, choices=AUDIENCES)
    p.add_argument("--by", required=True, help="签发人")
    p.add_argument("--valid-from", default=None, help="勘误适用期起（ISO 8601）")
    p.add_argument("--valid-to", default=None, help="勘误适用期止（ISO 8601）")
    p.add_argument("--dry-run", action="store_true", help="只计算影响，不落事件")

    p = sub.add_parser("render", help="编辑 API：按受众与日期给出当时有效表述")
    p.add_argument("--term-id", required=True)
    p.add_argument("--audience", required=True, choices=AUDIENCES)
    p.add_argument("--at", required=True, help="查询日期（ISO 8601）")

    p = sub.add_parser("render-passage", help="渲染段落在某日期的有效文本")
    p.add_argument("--passage-id", required=True)
    p.add_argument("--at", default=None, help="查询日期（ISO 8601）")

    p = sub.add_parser("audit", help="从中文段落回到原文、证据与历次取舍")
    p.add_argument("--passage-id", default=None)
    p.add_argument("--text", default=None, help="按中文片段定位段落")

    sub.add_parser("pending", help="列出待会签的审定请求")
    return parser


def _dispatch(args: argparse.Namespace, registry: Registry):
    if args.command == "register-term":
        term = registry.register_term(
            term_id=args.term_id,
            lemma=args.lemma,
            transliteration=args.transliteration,
            semantic_range=args.semantic_range,
            provenance=args.provenance,
            dating_context=args.dating_context,
            forbidden=_pairs(args.forbid),
            registered_by=args.by,
        )
        return {"term_id": term.term_id}
    if args.command == "propose":
        candidate = registry.propose_candidate(
            candidate_id=args.candidate_id,
            term_id=args.term_id,
            text=args.text,
            audience=args.audience,
            proposed_by=args.by,
            note=args.note,
        )
        return {"candidate_id": candidate.candidate_id}
    if args.command == "review-open":
        review, created = registry.open_review(
            request_id=args.request_id,
            candidate_id=args.candidate_id,
            submitted_by=args.by,
            provenance=args.provenance,
            urgent=args.urgent,
        )
        return {"request_id": review.request_id, "created": created, "status": review.status}
    if args.command == "review-opinion":
        review = registry.record_opinion(
            request_id=args.request_id,
            role=args.role,
            reviewer=args.by,
            verdict=args.verdict,
            rationale=args.rationale,
        )
        return {"request_id": review.request_id, "status": review.status}
    if args.command == "passage-register":
        passage = registry.register_passage(
            passage_id=args.passage_id,
            carrier=args.carrier,
            audience=args.audience,
            text_zh=args.text,
            term_usages=[
                {"term_id": key, "rendering": value} for key, value in _pairs(args.use).items()
            ],
        )
        return {"passage_id": passage.passage_id, "version": passage.version}
    if args.command == "passage-publish":
        passage = registry.publish_passage(passage_id=args.passage_id)
        return {"passage_id": passage.passage_id, "published_at": passage.published_at}
    if args.command == "revise":
        if args.dry_run:
            return {"impact": registry.impact_analysis(term_ids=args.term, audience=args.audience)}
        return registry.apply_revision(
            term_ids=args.term,
            audience=args.audience,
            issued_by=args.by,
            valid_from=args.valid_from,
            valid_to=args.valid_to,
        )
    if args.command == "render":
        return registry.effective_rendering(
            term_id=args.term_id, audience=args.audience, at=args.at
        ) or {"text": None}
    if args.command == "render-passage":
        return registry.render_passage(passage_id=args.passage_id, at=args.at)
    if args.command == "audit":
        if args.passage_id:
            return registry.audit_passage(passage_id=args.passage_id)
        if args.text:
            return [
                registry.audit_passage(passage_id=passage_id)
                for passage_id in registry.find_passages(args.text)
            ]
        raise RegistryError("审计需要 --passage-id 或 --text")
    if args.command == "pending":
        return registry.pending_reviews()
    raise RegistryError(f"未知命令：{args.command}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    registry = Registry(args.log)
    try:
        result = _dispatch(args, registry)
    except RegistryError as error:
        print(f"被拒绝：{error}", file=sys.stderr)
        return 1
    if result is not None:
        _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
