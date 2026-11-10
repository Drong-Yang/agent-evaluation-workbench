# 六题基础回归示例

这个小示例用确定性分支模拟“选择第二款 → 查询实时价格库存 → 加购 → 检查状态”，用于展示缺陷修复前后的回归对比。只使用 Python 标准库，不调用真实模型，不测自然语言理解或真实商品召回。

## 运行

在仓库根目录执行：

```bash
python -B examples/shopping_eval/run_eval.py --version baseline
python -B examples/shopping_eval/run_eval.py --version fixed
```

分别生成本目录的 [baseline 报告](report_baseline.json) 与 [fixed 报告](report_fixed.json)，再次运行会覆盖对应文件。

基线规则通过数为 5/6，修正后为 6/6。唯一决策改动是 `shopping_004` 遇到“接口明确返回失败”时，不再宣称已经加购成功。数字仅描述六道确定性模拟题，不代表真实模型准确率或线上效果。

## 用例与判定

[cases.json](cases.json) 包含正常、超预算、缺少候选列表、明确失败、已有数量和缺货六题。[run_eval.py](run_eval.py) 的 `run_agent` 不读取预期答案；`grade` 对目标商品、数量增量、其他商品、前置查询依据、调用轨迹与完成声明做检查。

商品均为虚构数据，价格以整数分保存。每题新建 `ShoppingWorld`，初始两个再加一个应变为三个；同请求 ID 的重试不重复增加，新的加购意图使用新 ID。加购不代表下单付款。

`semantic_review=pending` 表示自然语言解释仍需人工或模型复核。完整三轮真实模型、检索故障、超时恢复与攻击场景见[七维评测](../seven_dimensions/README.md)。
