# 联合考古上下文版本库

本仓库维护该服务跨模块交换时使用的领域事件信封、中文样例和基础校验入口，使不同业务组件能够用一致的对象标识、事件版本和发生时间传递事实。

## 目录

- `contracts/domain.schema.json`：领域对象与事件名称约定。
- `data/sample.json`：一条可用于本地联调的示例事件。
- `data/sample_bilateral_review.json`：一条双边审定采用决定的示例事件。
- `src/envelope.py`：事件信封的基础字段校验。
- `src/cli.py`：检查 JSON 事件文件的命令入口。
- `src/registry/`：双语术语与叙事版本库（术语登记、候选、双边会签、影响分析、迁移与勘误、审计）。
- `src/registry_cli.py`：版本库的编辑与审计命令入口。
- `tests/`：信封、命令入口与版本库的回归测试。

## 领域约定

当前交换协议覆盖双语术语、语境证据和分层叙事。事件标识一旦接收不得原地复用为另一份内容，版本必须为正整数，时间采用带时区的 ISO 8601 格式。业务修订通过新的事件表达，原始记录继续用于追溯。

版本库在事件信封之上落实术语治理闭环：

- 登记源语术语时记录原文词形、转写、语义范围、出处、年代语境与禁用误译；命中禁用误译的候选直接拒绝。
- 译者提出中文候选；埃及学者确认原语语义，中国策展人判断本地表达。采用决定必须留下两方意见，两方意见须由不同人员签署，提交人不得独自完成提交和终审；紧急纠错标记不豁免责任签署。
- 审定请求按（候选、出处）指纹幂等：相同请求重复到达返回既有决定，编号相同但候选或出处变化进入争议，争议中的请求不得签署。
- 术语修订先计算对展签、图录、讲稿和课程材料的影响（`revise --dry-run`）：未发布段落整体迁移，同一段落中的多个术语在一次事件中切换，校验失败则整段不落事件；已经印刷或讲授的段落只追加带适用期的勘误，原文不动。
- 勘误按内容指纹去重，服务恢复后重跑同一修订不会重复发出勘误；待会签的审定在恢复后可继续签署。
- 编辑查询按受众与发布日期给出当时有效表述；审计命令从一段中文回到原文词形、证据与历次取舍。

## 本地运行

检查示例事件：

```bash
python3 -m src.cli data/sample.json
python3 -m src.cli data/sample_bilateral_review.json
```

版本库命令（`--log` 指定事件日志，缺省 `data/registry.jsonl`）：

```bash
# 登记术语并提出候选
python3 -m src.registry_cli register-term --term-id TERM-RA --lemma "rꜥ" \
    --transliteration "Ra" --semantic-range "太阳神名，亦指太阳本身" \
    --provenance "《亡灵书》第15章" --dating-context "新王国时期" \
    --forbid "拉神灯=混淆神名与器物" --by translator:li
python3 -m src.registry_cli propose --candidate-id CAND-1 --term-id TERM-RA \
    --text "拉神" --audience public_adult --by translator:li

# 双边会签
python3 -m src.registry_cli review-open --request-id REQ-1 --candidate-id CAND-1 --by editor:zhao
python3 -m src.registry_cli review-opinion --request-id REQ-1 \
    --role egyptian_semantics --by egyptology:farouk --verdict confirm --rationale "原语语义确认"
python3 -m src.registry_cli review-opinion --request-id REQ-1 \
    --role chinese_expression --by curator:wang --verdict confirm --rationale "本地表达妥当"

# 影响分析、修订、按日期渲染与审计
python3 -m src.registry_cli revise --term TERM-RA --audience public_adult --by editor:zhao --dry-run
python3 -m src.registry_cli render --term-id TERM-RA --audience public_adult --at 2026-10-01T00:00:00+08:00
python3 -m src.registry_cli audit --text "拉神"
python3 -m src.registry_cli pending
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
