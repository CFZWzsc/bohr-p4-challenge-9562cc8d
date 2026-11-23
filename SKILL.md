---
name: research-figure-planner
description: Use when a research data-analysis case supplies tabular measurements and asks for a publication-ready figure plan, chart choice, encoding, labels, caption, color and export settings, or an SVG preview; run scripts/run_skill.py for a deterministic result.json plus figure.svg.
---

# 科研图表设计与可视化

## 用途与适用条件

在科研作图任务中使用：根据数据结构选择图型、给出编码映射、坐标轴与单位、色盲友好配色、图注与导出参数，并生成可检查的 SVG 预览。输入为 `/input/case.json`、可选 `/input/task.md` 与 CSV/TSV 数据。

不适用：需要位图渲染（PNG/TIFF）、期刊模板排版、交互式仪表盘、图像处理。脚本不依赖 matplotlib，输出矢量 SVG 与完整作图规格。

## 输入

- `case.json`：`case_id`、`output_contract`；可选 `x_column`、`y_column`、`group_column`、`chart_type`、`journal`。
- 数据：CSV/TSV，自动识别分隔符与编码；数值列允许 80% 可解析；缺失行剔除并计数。
- 输出：`/app/output/result.json` 与 `/app/output/figure.svg`。

## 处理流程（对应评分维度）

1. **数据画像（任务完成度）**：逐列判断数值/分类/日期，记录缺失、去重后类别数与样本量；不把 ID 列当连续变量。
2. **图型选择（正确性）**：一个分类 + 一个数值 → 分组柱状图（均值±SD）；两个数值 → 散点图并给出最小二乘拟合；仅一个数值 → 直方图（Sturges 分箱）；日期 + 数值 → 折线图。选择依据写入 `recommendation.rationale`。
3. **编码与轴（规范性）**：明确 x、y、颜色、误差线含义；轴标题取列名并提取括号内单位；坐标范围按数据留 5% 边距，柱状图从 0 起。
4. **可读性（质量）**：类别数 ≤ 12；标签截断并保留完整名于 `labels.full_labels`；检查缺失比例、常量列、超出画布的点，写入 `checks`。
5. **图注（完整性）**：自动生成含 n、误差定义、统计口径的图注骨架；不写未计算出的显著性星号。
6. **导出规格（可核验性）**：单栏 89 mm / 双栏 183 mm、300 dpi、字体 Arial/Helvetica 7–9 pt、色盲友好 Okabe-Ito 调色板；SVG 为真实生成文件，PNG 明确标注未生成。
7. **失败与恢复（可靠体验）**：无数值列时降级为表格/条形计数并说明；空数据、全缺失、列名冲突进入 `errors`、`checks`，不伪造图形。

## 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| `output_contract` 各字段 | 命中别名时填真实值，否则 `null` + `contract_notes` |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | `recommendation`、`encoding`、`aggregates`、`labels`、`caption`、`style`、`exports`、`checks` |
| `evidence` | 数据文件哈希、列类型、样本量 |
| `uncertainty` | 图型启发式、未做统计检验、无位图输出 |
| `errors` / `stats` | 真实错误与计数 |

## 异常处理表

| 情况 | 处置 | 状态 |
|-|-|-|
| case.json 缺失/非法 | 空结果 + 错误 | `failed` |
| 无数据文件 | 空结果并说明 | `insufficient_input` |
| 无数值列 | 降级为类别计数条形图 | `partial` |
| 单列且常量 | 生成规格但不画有效差异，写入 `checks` | `partial` |
| 缺失比例 > 20% | 标注结果不稳，仍出图 | `partial` |
| 类别 > 12 | 保留全部数据但建议合并/分面 | `partial` |
| 需要 PNG | `exports.png=null` 并说明无光栅化器 | `success` |
| 数据为空 | 不生成 SVG | `insufficient_input` |

## 限制

- 只生成 SVG，不生成 PNG/TIFF；不嵌入期刊模板。
- 图型选择与配色为规则化建议，需作者按学科惯例复核。
- 不计算显著性标记、不替代统计检验；误差线含义由 `encoding.error_bar` 明确声明。
- 不处理多面板拼图与双 y 轴。

## 最小调用示例（示例，非官方案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

示意输入 `{"case_id":"synthetic-demo","output_contract":{"recommendation":"object","caption":"string"}}`；示意输出保留 `case_id`、`recommendation`、`caption`、`status`，无数据时为 `insufficient_input`。仅为接口示例。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
