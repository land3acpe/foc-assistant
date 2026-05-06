"""网络工具：web_search, web_fetch, download_file"""

import os
from pathlib import Path

from config import PROJECT_ROOT, DESKTOP
from tools._common import _resolve_path

def _web_search(args: dict) -> str:
    """使用 DuckDuckGo 搜索网页"""
    query = args["query"]
    max_results = min(int(args.get("max_results", 5)), 10)

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)

        if not results:
            return f"未找到相关结果: '{query}'\n提示: 请尝试不同的关键词"

        output = [f"搜索结果 ({len(results)} 条): '{query}'\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            href = r.get("href", "")
            body = r.get("body", "")[:200]
            output.append(f"{i}. {title}\n   URL: {href}\n   {body}\n")

        return "\n".join(output)
    except ImportError:
        return "错误: 请安装 ddgs 库: pip install ddgs"
    except Exception as e:
        return f"搜索失败: {e}"


def _web_fetch(args: dict) -> str:
    """抓取网页文本内容"""
    url = args["url"]

    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除脚本和样式
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # 清理空行
        lines = [line for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

        if len(text) > 4000:
            text = text[:4000] + "\n\n... (内容过长已截断)"

        return f"网页内容: {url}\n{len(text)} 字符\n\n{text}"
    except ImportError as e:
        return f"错误: 缺少依赖库: {e}"
    except Exception as e:
        return f"抓取失败: {e}"


def _download_file(args: dict) -> str:
    """下载文件到安全目录。"""
    url = args.get("url", "")
    path_str = args.get("path", "")
    if not url or not path_str:
        return "错误: 缺少 url 或 path 参数"

    path = _resolve_path(path_str)
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        with requests.get(url, headers=headers, timeout=30, stream=True) as resp:
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 80 * 1024 * 1024:
                        return "下载失败: 文件超过 80MB 安全上限"
                    f.write(chunk)
        return f"文件已下载: {path} ({total} bytes)"
    except ImportError as e:
        return f"错误: 缺少依赖库: {e}"
    except Exception as e:
        return f"下载失败: {e}"
