#!/bin/zsh
cd "$(dirname "$0")"

echo "OLED 文献遍历器"
echo "项目目录：$(pwd)"
echo

if [ ! -x ".venv/bin/python" ]; then
  echo "[首次运行] 创建本地虚拟环境 .venv ..."
  python3 -m venv .venv || {
    echo "创建虚拟环境失败。请确认 macOS 上 python3 可用。"
    read "?按回车退出..."
    exit 1
  }

  echo "[首次运行] 安装依赖 ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || {
    echo "依赖安装失败。"
    read "?按回车退出..."
    exit 1
  }
fi

echo "开始读取 input/seed_doi.csv 并遍历..."
echo
.venv/bin/python run.py

echo
echo "运行结束。"
echo "PDF：papers/pdf/"
echo "补充材料：papers/supplements/"
echo "已处理列表：data/visited.csv"
echo "机构权限/人工队列：data/institution_queue.jsonl"
echo
read "?按回车退出..."
