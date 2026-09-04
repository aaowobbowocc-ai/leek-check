"""
Discord Webhook 通知 — 把晨報送到 Discord 頻道。

設定步驟（用戶端）：
  1. Discord 伺服器內 → 設定齒輪 → 整合 → 建立 Webhook
  2. 取名（如 "INVEST 晨報"）→ 選 channel → 複製 Webhook URL
  3. 加到 config/.env：
       DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>

無需 Discord bot、無需 API key。

設計原則：
  - 短訊息（< 1900 chars）：直接 post 為 content
  - 長訊息（≥ 1900 chars）：擷取摘要為 content + 全文當 .md 附件
  - 失敗不拋例外，回傳 False（呼叫端決定 fallback）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DISCORD_MAX_CONTENT = 1900     # 留 100 字 buffer for markdown
ALLOWED_STATUS = (200, 204)


class DiscordNotifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or ""

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("https://"))

    def send(
        self,
        content: str,
        file_path: Path | None = None,
        username: str | None = "INVEST Bot",
    ) -> bool:
        """
        送訊息（含可選附檔）。回傳成功 / 失敗。
        - content < 1900 字：直接 post
        - content ≥ 1900 字：擷取前 1900 字 + 完整內容當附檔
        - file_path 指定且存在：當作附檔
        """
        if not self.is_configured():
            logger.warning("Discord webhook 未設定，跳過")
            return False

        # 自動擷取 + 附檔
        attach_full_md = False
        if len(content) > DISCORD_MAX_CONTENT:
            preview = content[:DISCORD_MAX_CONTENT] + "\n\n_…（完整內容見附件）_"
            attach_full_md = True
        else:
            preview = content

        payload: dict = {"content": preview}
        if username:
            payload["username"] = username

        opened_files: list = []
        try:
            files: dict | None = None

            if file_path and file_path.exists():
                f = open(file_path, "rb")
                opened_files.append(f)
                files = {"file": (file_path.name, f, "text/markdown")}
            elif attach_full_md:
                # 把長 content 當作 file 附上
                files = {"file": ("morning_briefing.md", content.encode("utf-8"), "text/markdown")}

            if files:
                resp = requests.post(
                    self.webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                    timeout=30,
                )
            else:
                resp = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=30,
                )

            ok = resp.status_code in ALLOWED_STATUS
            if not ok:
                logger.warning("Discord webhook 失敗 status=%s body=%s",
                               resp.status_code, resp.text[:200])
            return ok

        except Exception as exc:
            logger.warning("Discord webhook 例外: %s", exc)
            return False
        finally:
            # 即使 post raise 也要關檔避免 handle leak
            for f in opened_files:
                try:
                    f.close()
                except Exception:
                    pass

    def send_briefing(
        self,
        date_str: str,
        report_md_path: Path,
    ) -> bool:
        """送晨報：按 H2 章節切分 + 多訊息推 + 完整 .md 附件。

        每個 ## section 一則訊息，section 內容超過 1900 字再切。
        最後一則訊息附上完整 .md 檔。
        """
        if not report_md_path.exists():
            logger.warning("Briefing 檔不存在: %s", report_md_path)
            return False
        full_content = report_md_path.read_text(encoding="utf-8")

        # 切 sections：以 \n## 為分界
        chunks = self._split_briefing_to_chunks(full_content, date_str)

        if not chunks:
            return self.send(f"📊 **INVEST 晨報 {date_str}**", file_path=report_md_path)

        all_ok = True
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            file_to_attach = report_md_path if is_last else None
            ok = self.send(chunk, file_path=file_to_attach)
            if not ok:
                all_ok = False
        return all_ok

    def send_briefing_action_only(
        self,
        date_str: str,
        report_md_path: Path,
    ) -> bool:
        """送晨報（精簡版）：只推「今天動作」摘要 + 完整 .md 附件。"""
        if not report_md_path.exists():
            logger.warning("Briefing 檔不存在: %s", report_md_path)
            return False
        full_content = report_md_path.read_text(encoding="utf-8")
        summary = self._extract_action_summary(full_content, date_str)
        return self.send(summary, file_path=report_md_path)

    @staticmethod
    def _extract_action_summary(content: str, date_str: str) -> str:
        """從晨報抽出「今天動作」+「三秒鐘決策」+ 觸發中的 paper 訊號。

        其餘資訊（市場狀態、宏觀、配置、Alpha Decay 等）全部留在附件。
        """
        import re

        parts: list[str] = [f"📊 **INVEST 晨報 {date_str}**", ""]

        m = re.search(r"## 🎯 今天該做什麼.*?(?=\n## |\Z)", content, re.S)
        if m:
            parts.append(m.group(0).strip())
            parts.append("")

        m = re.search(r"## ⚡ 三秒鐘決策區.*?(?=\n## |\Z)", content, re.S)
        if m:
            parts.append(m.group(0).strip())
            parts.append("")

        # Paper 訊號 section 全留附件 — 只在「實際有觸發」時才簡述代號
        # (檢測：## 🟢 觸發 / **觸發**/ **新訊號** 字眼出現)
        triggered: list[str] = []
        for label, pat in [
            ("ORB", r"## 🎯 \[Paper\] ORB.*?(?=\n## |\Z)"),
            ("量縮跌停", r"## 📉 \[Paper\] 量縮跌停.*?(?=\n## |\Z)"),
            ("法人", r"## 📡 \[Paper\] 法人.*?(?=\n## |\Z)"),
        ]:
            m = re.search(pat, content, re.S)
            if not m:
                continue
            block = m.group(0)
            # 抽取明確觸發代號 (e.g. 「✅ 觸發 2330 進場」)
            hits = re.findall(r"(?:✅|🟢)\s*觸發.*?(\d{4,6}[A-Z]?)", block)
            if hits:
                triggered.append(f"{label}: {', '.join(set(hits))}")
        if triggered:
            parts.append("**🚨 Paper 訊號觸發**")
            parts.extend(f"• {t}" for t in triggered)
            parts.append("")

        parts.append("📎 _完整晨報（市場/宏觀/Paper 策略/Alpha Decay 等）見附件 .md_")
        result = "\n".join(parts)
        if len(result) > DISCORD_MAX_CONTENT:
            result = result[:DISCORD_MAX_CONTENT - 50] + "\n\n_…完整見附件_"
        return result

    @staticmethod
    def _split_briefing_to_chunks(content: str, date_str: str) -> list[str]:
        """把晨報切成多個 < 1900 字的訊息。

        優先順序：
          1. ## H2 邊界（頂層 section）
          2. ### H3 邊界（子 section，如「📋 進出場建議」）
          3. 個股 bullet（以 "  - **" 開頭）
          4. 純行邊界（最後 fallback）
        """
        max_per_chunk = DISCORD_MAX_CONTENT

        # Step 1: H2 切分
        h2_sections: list[str] = []
        current: list[str] = []
        for line in content.splitlines():
            if line.startswith("## ") and current:
                h2_sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            h2_sections.append("\n".join(current))

        # Step 2: 對超長的 H2，按 H3 切；對超長的 H3，按個股 bullet 切
        chunks: list[str] = []
        for sec in h2_sections:
            if len(sec) <= max_per_chunk:
                chunks.append(sec)
                continue

            # H3 切
            h3_parts: list[str] = []
            buf: list[str] = []
            for ln in sec.splitlines():
                if ln.startswith("### ") and buf:
                    h3_parts.append("\n".join(buf))
                    buf = [ln]
                else:
                    buf.append(ln)
            if buf:
                h3_parts.append("\n".join(buf))

            for part in h3_parts:
                if len(part) <= max_per_chunk:
                    chunks.append(part)
                    continue

                # 個股 bullet 切（"  - **XXXX 名稱**" 為起點）
                bullet_parts = DiscordNotifier._split_by_stock_bullets(part, max_per_chunk)
                chunks.extend(bullet_parts)

        # Header 加在第 1 段
        header = f"📊 **INVEST 晨報 {date_str}**\n\n"
        if chunks:
            chunks[0] = header + chunks[0]

        return chunks

    @staticmethod
    def _split_by_stock_bullets(text: str, max_len: int) -> list[str]:
        """把長段按「個股 bullet」分塊（保留前綴 header）。

        個股 bullet 形如:
          "  - **1570 力肯** 🟢 高信心..."
        切完每個 stock 自己一則訊息（連帶其縮排子行）。
        """
        lines = text.splitlines()
        # 找 prefix（第一個 stock bullet 出現前的內容）
        prefix_lines: list[str] = []
        idx = 0
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            # 偵測形如 "- **NNNN " 的個股 bullet
            if stripped.startswith("- **") and len(stripped) > 6:
                # 確認 NNNN 是數字（個股代號）
                token = stripped[4:].split()[0] if " " in stripped[4:] else ""
                if token and any(c.isdigit() for c in token):
                    idx = i
                    break
            prefix_lines.append(ln)
        else:
            # 沒找到 stock bullet，直接 fallback line-by-line
            return DiscordNotifier._split_by_lines(text, max_len)

        prefix = "\n".join(prefix_lines)

        # 把 idx 之後切成多個 bullet groups
        bullet_groups: list[list[str]] = []
        current_bullet: list[str] = []
        for ln in lines[idx:]:
            stripped = ln.lstrip()
            is_new_bullet = stripped.startswith("- **") and any(
                c.isdigit() for c in (stripped[4:].split()[0] if " " in stripped[4:] else "")
            )
            if is_new_bullet and current_bullet:
                bullet_groups.append(current_bullet)
                current_bullet = [ln]
            else:
                current_bullet.append(ln)
        if current_bullet:
            bullet_groups.append(current_bullet)

        # 組裝：prefix + 每個 bullet group 為一則訊息（多個 bullet 合併直到接近 max_len）
        chunks: list[str] = []
        if prefix.strip():
            chunks.append(prefix)

        buf: list[str] = []
        buf_len = 0
        for group in bullet_groups:
            group_text = "\n".join(group)
            group_len = len(group_text) + 1
            if buf and buf_len + group_len > max_len:
                chunks.append("\n".join(buf))
                buf = list(group)
                buf_len = group_len
            else:
                buf.extend(group)
                buf_len += group_len
        if buf:
            chunks.append("\n".join(buf))

        # 若有單一 bullet > max_len，再 fallback line-cut
        final: list[str] = []
        for c in chunks:
            if len(c) <= max_len:
                final.append(c)
            else:
                final.extend(DiscordNotifier._split_by_lines(c, max_len))
        return final

    @staticmethod
    def _split_by_lines(text: str, max_len: int) -> list[str]:
        """純行切割 fallback。"""
        chunks: list[str] = []
        buf: list[str] = []
        buf_len = 0
        for ln in text.splitlines():
            if buf_len + len(ln) + 1 > max_len and buf:
                chunks.append("\n".join(buf))
                buf = [ln]
                buf_len = len(ln) + 1
            else:
                buf.append(ln)
                buf_len += len(ln) + 1
        if buf:
            chunks.append("\n".join(buf))
        return chunks
