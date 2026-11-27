# OLED 文献遍历器 v0.2

## 当前版本做什么

点击 `一键运行.command` 后：

1. 从 `input/seed_doi.csv` 读取种子 DOI。
2. 用 OpenAlex 查询论文元数据。
3. 如果 OpenAlex 提供合法的 Open Access（开放获取，OA）PDF 地址，自动保存到 `papers/pdf/`。
4. 对公开可访问的文章页面做“尽力而为”的 Supplementary / Supporting Information 链接发现，并保存到 `papers/supplements/`。
5. 使用 OpenAlex `related_works` 获取相似/相关论文，将其 DOI 加入下一层队列。
6. 最多遍历到深度 3，并且 DOI 去重。
7. 默认最多处理 100 篇，防止推荐关系指数膨胀。
8. 没有自动取得全文的文章进入 `data/institution_queue.jsonl`，以后集中通过学校权限处理。

## 目录

```text
oled_literature_traverser/
├── 一键运行.command
├── run.py
├── config.json
├── requirements.txt
├── input/
│   └── seed_doi.csv
├── papers/
│   ├── pdf/
│   └── supplements/
├── data/
│   ├── visited.csv
│   ├── discovered.csv
│   ├── institution_queue.jsonl
│   └── failed.jsonl
└── logs/
    └── run.log
```

> 按要求，本包不包含 Windows `.cmd` 文件。

## 种子 DOI

当前预填了：

- `10.1002/advs.202303504`
- `10.3389/fchem.2020.00716`

可以直接编辑：

`input/seed_doi.csv`

继续添加 DOI。

## 遍历深度

`config.json`：

```json
{
  "max_depth": 3,
  "max_papers": 100,
  "max_related_per_paper": 5
}
```

含义：

- 种子论文：depth 0
- 第一轮相关论文：depth 1
- 第二轮：depth 2
- 第三轮：depth 3
- 到 depth 3 不再继续扩展。

## Supplementary Information

补充材料自动发现属于“尽力而为”：

- 优先看 Crossref relation 元数据。
- 如果文章 landing page 无需登录、可以普通访问，则查找页面中明确写有 Supplement / Supporting Information 的链接。
- 不登录 CARSI。
- 不解决 CAPTCHA。
- 不绕过付费墙。
- 不模拟用户规避出版社反爬。

所以部分出版社的 supplement 可能无法自动发现，这些可以后续针对具体出版社增加官方 API / 明确公开接口。

## 第一次运行

双击：

`一键运行.command`

首次会在项目目录自动创建：

`.venv/`

并安装 `requests`、`beautifulsoup4`。

如果 macOS 第一次阻止运行：

1. Finder 中右键 `一键运行.command`
2. 选择“打开”
3. 再确认“打开”

## 输出

### `data/visited.csv`

记录：

- DOI
- 标题
- 年份
- 期刊
- 遍历深度
- 父 DOI
- OA 状态
- PDF 下载状态
- Supplement 数量
- Related 数量
- 引用数
- landing page
- OpenAlex ID

这张表以后可以直接进入 OLED 文献知识摄取管线。

### `data/discovered.csv`

记录“哪篇论文发现了哪篇相关论文”，后续可以转成文献发现图。

### `data/institution_queue.jsonl`

无法自动获得 OA PDF 的论文进入这里，用于以后集中通过 CARSI / 学校 IP 补全文。

## 注意

这个工具的目标是自动处理公开元数据和公开全文，不用于连续、系统地绕过出版社访问控制或学校电子资源限制。
