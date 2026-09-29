# 联合考古上下文版本库

本仓库维护中埃联合考古团队共用的双语术语与叙事版本库：登记源语术语与中文候选，以双边审定控制采用，以事件日志驱动术语修订、段落迁移与勘误，使展签、图录、讲稿和课程材料中的同一概念在所有载体里保持一致。

## 目录

- `contracts/domain.schema.json`：领域对象与事件名称约定。
- `data/sample.json`：一条可用于本地联调的示例事件。
- `src/envelope.py`：事件信封的基础字段校验。
- `src/store.py`：只追加的事件日志，按聚合维护递增版本，JSONL 持久化。
- `src/terms.py`：源语术语登记与中文候选。
- `src/review.py`：双边审定（会签、幂等、争议）。
- `src/passages.py`：叙事段落版本、载体与发布状态。
- `src/revision.py`：术语修订的影响计算、会签、迁移与勘误。
- `src/query.py`：按受众和日期的编辑查询与审计追溯。
- `src/registry.py`：统一门面与服务恢复入口。
- `src/cli.py`：校验、审计、有效表述查询与恢复续办的命令入口。
- `tests/`：各模块与命令入口的回归测试。

## 领域约定

事件标识一旦接收不得原地复用为另一份内容，版本必须为正整数，时间采用带时区的 ISO 8601 格式。业务修订通过新的事件表达，原始记录继续用于追溯。

事件类型：`TERM_REGISTERED`、`CANDIDATE_PROPOSED`、`BILATERAL_REVIEWED`、`PASSAGE_REGISTERED`、`PASSAGE_STATE_CHANGED`、`PASSAGE_MIGRATED`、`ERRATA_ISSUED`、`REVISION_PREPARED`、`REVISION_SIGNED`、`REVISION_APPLIED`。聚合类型：`source_term`、`translation_candidate`、`narrative_passage`、`errata_release`、`term_revision`。

## 工作流

1. **术语登记**：记录原文词形、转写、语义范围、出处、年代语境与禁用误译；译者按受众层级（学术/公众/儿童）提出中文候选，命中禁用误译的候选直接拒绝。
2. **双边审定**：埃及学者确认原语语义，中国策展人判断本地表达，两方意见全部留痕后方可采用；提案人与提交人不得担任审定人，两方审定不得由同一人完成。相同审定请求重复到达返回既有决定；编号相同但候选或出处变化进入争议。
3. **段落登记**：段落由文本片段与术语引用组成，载体为展签、图录、讲稿或课程；引用未审定候选或受众层级不一致的候选会被拒绝。状态沿 未发布 → 已发布 → 已印刷/已讲授 流转。
4. **术语修订**：先计算对各载体的影响；未发布内容迁移到新版本，已印刷或讲授的版本只追加勘误和适用期。同一句话中的多个术语在一次事务中切换，失败不留半新半旧的译文。修订须经埃方与中方会签，紧急纠错也不能跳过责任签署。
5. **查询与审计**：编辑查询按受众和发布日期给出当时有效表述（自动叠加适用期内的勘误）；审计命令从一段中文回到原文词形、出处证据与历次取舍。服务恢复后重放事件日志，继续待会签修订，且不重复发出勘误。

## 本地运行

检查示例事件（兼容旧用法）：

```bash
python3 -m src.cli data/sample.json
python3 -m src.cli check data/sample.json
```

从中文段落追溯原文、证据与历次取舍：

```bash
python3 -m src.cli audit <事件日志.jsonl> <段落ID>
```

按发布日期查询当时有效表述：

```bash
python3 -m src.cli effective <事件日志.jsonl> <段落ID> --date 2026-10-15T00:00:00+08:00
```

服务恢复后继续待会签修订：

```bash
python3 -m src.cli resume <事件日志.jsonl>
```

运行测试：

```bash
python3 -m unittest discover -s tests
```

编译检查：

```bash
python3 -m compileall -q src tests
```

这些命令只使用 Python 标准库，不需要单独运行数据库或其他服务。
