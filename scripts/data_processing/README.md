# Dialogue data processing

离线数据处理脚本放在本目录，避免与 `src/` 下的应用运行代码混合。

## 提取 ECD 正样本

在项目根目录运行：

```bash
python3 scripts/data_processing/extract_positive_dialogues.py
```

脚本会：

1. 读取 `data/dialogue_dataset/raw_samples/` 中的 `train.tsv`、`valid.tsv` 和 `test.tsv`；
2. 只保留 `label=1` 的完整对话；
3. 按完整 `utterances` 去重；
4. 合并重复记录的来源 split 和原始行号；
5. 输出 `data/dialogue_dataset/processed/positive_samples.json`。

原始 TSV 文件不会被修改。

## 关键词初筛售后对话

先编辑关键词配置：

```text
data/dialogue_dataset/config/screening_keywords.json
```

然后在项目根目录运行：

```bash
python3 scripts/data_processing/screen_after_sales_dialogues.py
```

输出文件为：

```text
data/dialogue_dataset/processed/candidate_samples.json
```

筛选会忽略 ECD 分词产生的空格。例如关键词 `没收到` 可以匹配原文中的
`没 收到`。每条候选会记录命中的类别和关键词，并预留人工审核字段：

```json
{
  "review_status": "Pending",
  "review_notes": null
}
```

人工审核结论保存在：

```text
data/dialogue_dataset/reviews/manual_reviews.json
```

`review_status` 可设为 `Keep`、`Reject` 或 `Rewrite`。筛选脚本会在每次运行时
合并这份人工审核文件，因此重新生成 `candidate_samples.json` 不会丢失审核结论。
