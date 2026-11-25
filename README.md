# research-figure-planner

面向出版级图表的方案生成：图型选择、编码映射、坐标轴与图例、标题与图注、配色与导出设置，并产出可预览 SVG。

## 安装与运行

- 依赖：Python 3.9+ 标准库，**无第三方依赖**，无需联网。
- 包内自带合成演示案例（`examples/demo-case/`），可直接跑通：

```powershell
python scripts/run_skill.py --case examples/demo-case/case.json --out "$env:TEMP\demo\result.json" --work "$env:TEMP\demo\work"
```

- `--out` 与 `--output` 等价；默认读 `/input/case.json`，写 `/app/output/result.json`。

## 输入

- `case.json`：`case_id`、`output_contract`，以及可选的输入定位键（`input_dir` / `materials` / `files` / `path`）。
- 材料：规则见 `SKILL.md` 的「输入」一节。

## 输出

- result.json
- figure.svg
- figure_plan（内嵌）

同时由 `scripts/report_kit.py` 产出交付自检件：`report.md`、`provenance.json`（输入 sha256/编码/分隔符/参数）、`repro_manifest.json`、`consistency_check`（写入 result.json），以及 `inventory.tsv`（当输入含 CSV/TSV 表时）。

## 保证

- **单次运行完成交付**：不存在两阶段或 `judgement_pending` 状态。
- **确定性**：同一输入连续两次运行，`result.json` 字节一致；无时间戳/随机数进入产物。
- **异常不崩溃**：case.json 损坏、空表、仅表头、非 UTF-8（GB18030）等输入均写出结构化 `result.json` 并以 0 退出，失败以 `status` 字段表达。
- **自检**：- SVG 元素数 > 0 且含坐标轴与数据标记；plan 字段与 SVG 编码一致

## 限制

- 不渲染位图；不替代制图软件
- 分组/哨兵值/异常值的处理假设在 assumptions 与 caption 中显式披露

## 可复用性

- `scripts/run_skill.py` 为入口，`scripts/report_kit.py` 为通用交付自检模块，可独立复用到同类数据分析包。
- 维护信息：接口与契约见 `SKILL.md`，评分维度映射见 `RUBRIC.md`，仓库地址见 `GITHUB_URL.txt`。
