# ECD Sample Data

本目录保存 DeepUtteranceAggregation 项目公开的 E-commerce Dialogue Corpus 样本文件，作为本项目对话筛选与试标注的原始输入。

## 来源

- Repository: <https://github.com/cooelf/DeepUtteranceAggregation>
- Paper: *Modeling Multi-turn Conversation with Deep Utterance Aggregation* (COLING 2018)
- Upstream directory: `ECD_sample/`
- Downloaded: 2026-08-13

## 文件

| 文件 | 原始文件 | 行数 | 用途 |
|---|---|---:|---|
| `train.tsv` | `ECD_sample/train` | 154 | 训练样本 |
| `valid.tsv` | `ECD_sample/valid` | 154 | 验证样本 |
| `test.tsv` | `ECD_sample/test` | 999 | 测试样本 |

每行采用 Tab 分隔：

```text
label \t conversation utterance 1 \t ... \t conversation utterance n \t response
```

其中 `label=1` 表示最后一列是匹配回复，`label=0` 表示候选回复不匹配。该数据原本用于 response selection，并不是已经标注好的退款/换货意图数据；本项目只把它作为自然语言语料来源，后续还需筛选、还原角色并进行二次标注。

## 使用注意

上游 README 要求使用时引用论文，但仓库未提供独立的 `LICENSE` 文件。在公开发布、再分发或商业使用前，需要进一步确认数据许可与使用边界。原始文件保持不修改，清洗和标注结果应写入其他文件。

推荐引用：

```bibtex
@inproceedings{zhang2018dua,
  title={Modeling Multi-turn Conversation with Deep Utterance Aggregation},
  author={Zhang, Zhuosheng and Li, Jiangtong and Zhu, Pengfei and Zhao, Hai},
  booktitle={Proceedings of the 27th International Conference on Computational Linguistics},
  pages={3740--3752},
  year={2018}
}
```
