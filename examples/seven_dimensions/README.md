# 七维评测运行说明

本模块提供真实豆包执行与离线 fixture 两种模式，业务工具、商品资料与购物车均为内存 Mock。先看[项目介绍](../../README.md)或[系统设计](../../docs/architecture.md)，数据字段见 [CONTRACT.md](CONTRACT.md)。

## 本地工作台

在仓库根目录执行：

```bash
python -B examples/seven_dimensions/server.py
```

打开 http://127.0.0.1:8765/，选择场景，查看“测试用例与评判标准”，选择运行模式，再点击“运行此 Case”。可切换对话过程、工具调用与检索证据，展开七维判定，并查看购物车变化与完整报告。

- 离线模式不需要密钥，不调用 API；固定 fixture 仅演示执行与评测流程。
- 真实模式需要根目录 `.env` 中的 `ARK_API_KEY` 与 `ARK_MODEL`，配置示例见 [.env.example](../../.env.example)。模型默认值为 `doubao-seed-2-1-lite-260915`。
- 界面支持每个场景重复 1–3 次。语义裁判为独立模型请求，会增加用量。
- 同时只运行一个任务。服务仅绑定 `127.0.0.1`；可通过 `--port 8766` 改端口。
- 修改 `.env` 后重启服务；终端按 `Ctrl+C` 停止。

## CLI

```bash
# 查看场景；不调用模型
python -B examples/seven_dimensions/run.py --list

# 离线模式，全部十个场景
python -B examples/seven_dimensions/run.py --offline --all

# 以下命令会产生真实 API 调用
python -B examples/seven_dimensions/run.py --probe
python -B examples/seven_dimensions/run.py --case normal
python -B examples/seven_dimensions/run.py --all --repeat 2

# 不调用语义裁判；程序规则仍执行，缺少的语义判定保持未知
python -B examples/seven_dimensions/run.py --case normal --no-judge
```

`--case` 与 `--all` 互斥；未指定场景时默认 `normal`。CLI 的 `--repeat` 范围为 1–20。`--max-steps` 限制每轮模型执行步骤，默认 8、范围 1–16；整次任务最多 30 次工具调用。

`--output-dir` 指定报告目录，默认 `reports/seven_dimensions/`。工作台生成的文件位于其 `workbench/` 子目录。每份 JSON 保存原始记录、逐维判定和批次汇总，同名 HTML 可独立打开，无需 CDN。报告由使用者在本地运行后生成。

## 配置与成本

`ARK_API_KEY` 仅由后端本地读取；`.env` 不应提交。运行器不会覆盖已存在的同名进程环境变量。

`ARK_INPUT_PRICE_PER_MILLION` 与 `ARK_OUTPUT_PRICE_PER_MILLION` 是可选估算单价，单位为元/百万 Token，只有确认适用于当前模型后才填写。没有 usage 或适用单价时保持未知；估算不能代替账单。Agent 与 Judge 的调用量、Token 和 API 耗时分别记录，Mock 工具耗时不代表真实业务服务性能。

## 怎样理解结果

- `source=live` 代表真实模型调用，`offline_fixture` 代表固定演示，两类数据分开统计。
- 通过、失败、未验证、不适用分别对应 `pass / fail / unknown / na`。一条维度包含多项检查，不能只看总颜色跳过细项。
- 单次运行无法判断稳定性；重复少量样本也不构成生产可靠性证据。
- 裁判与 Agent 使用同一模型的独立请求，尚需人工抽检校准。
- 检索按实际返回条数与本轮完整相关集合计算 Precision/Recall，后续追问只以实际选中商品资料为相关集合。这是受控 Mock 检索。
- CLI 退出码反映执行是否发生运行错误，不表示所有维度都通过。检索噪声或模型越权被检查器判失败，可能正是预期的故障检出演示。

## 离线测试

```bash
python -B -m unittest discover -s examples/seven_dimensions -p "test_*.py" -v
```

测试检查评测器、Mock 世界、执行器和请求边界，不调用真实 API。
