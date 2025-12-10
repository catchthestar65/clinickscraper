"""Claude APIによるクリニック情報検証"""

import json
import logging
from typing import TYPE_CHECKING

import anthropic
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.config import config
from app.exceptions import ValidationError

if TYPE_CHECKING:
    from app.models.clinic import Clinic

logger = logging.getLogger(__name__)


class ClaudeValidator:
    """Claude APIによるクリニック情報の検証"""

    VALIDATION_PROMPT = """以下のクリニック情報を検証してください。

{clinics_json}

各クリニックについて、以下の3点を判定してください：

1. is_official_site: URLがクリニックの公式サイトかどうか
   - ポータルサイト（EPARK、ホットペッパー等）→ false
   - 口コミサイト、比較サイト → false
   - クリニック公式サイト → true
   - URLが空の場合 → null

2. is_major_chain: アフィリエイト広告を大規模に出稿しているクリニックかどうか
   【除外すべきクリニック（true）の例】
   - AGAスキンクリニック、湘南美容クリニック、TCB東京中央美容外科
   - ゴリラクリニック、Dクリニック、クリニックフォア
   - イースト駅前クリニック、ウィルAGAクリニック、駅前AGAクリニック
   - DMMオンラインクリニック、AGAヘアクリニック
   - その他、比較サイトやアフィリエイトサイトで頻繁に紹介される大手

   【残すべきクリニック（false）の例】
   - スマイルAGAクリニック（ams-smile.co.jp）のような中小規模クリニック
   - 地域密着型の個人クリニック
   - 1〜10院程度の小規模チェーン
   - アフィリエイトサイトであまり見かけないクリニック

3. normalized_name: クリニック名の正規化（重複検出用）
   - 「〇〇院」「〇〇クリニック新宿」などの院名を除去
   - 例: "AGAスキンクリニック新宿院" → "AGAスキンクリニック"
   - 例: "スマイルAGAクリニック渋谷院" → "スマイルAGAクリニック"

重要: URLのドメインからクリニック名を正確に特定してください。
例: ams-smile.co.jp → スマイルAGAクリニック（湘南美容ではありません）

JSON配列形式で回答してください（コードブロック不要）：
[
  {{
    "index": 0,
    "is_official_site": true,
    "is_major_chain": false,
    "normalized_name": "クリニック名",
    "reason": "判定理由（簡潔に）"
  }},
  ...
]"""

    def __init__(self) -> None:
        api_key = config.anthropic_api_key
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY is not set")

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = config.claude_model
        self.batch_size = config.claude_batch_size

    def validate_batch(self, clinics: list["Clinic"]) -> list[dict]:
        """
        複数クリニックをバッチで検証

        Args:
            clinics: クリニック情報のリスト

        Returns:
            検証結果を含むクリニック情報のリスト（dict形式）
        """
        import time
        start_time = time.time()
        logger.info(f"[CLAUDE] 検証開始: {len(clinics)}件, モデル={self.model}, バッチサイズ={self.batch_size}")

        if not self.client:
            logger.warning("[CLAUDE] API client未初期化、検証スキップ")
            return [
                {**clinic.model_dump(), "is_valid": True, "validation_reason": "API未設定"}
                for clinic in clinics
            ]

        results: list[dict] = []
        total_batches = (len(clinics) + self.batch_size - 1) // self.batch_size

        # バッチに分割して処理
        for batch_num, i in enumerate(range(0, len(clinics), self.batch_size), 1):
            batch = clinics[i : i + self.batch_size]
            batch_start_index = i

            logger.info(f"[CLAUDE] バッチ {batch_num}/{total_batches} 処理中 ({len(batch)}件)...")

            try:
                batch_start = time.time()
                batch_results = self._validate_batch_internal(batch, batch_start_index)
                batch_elapsed = time.time() - batch_start
                logger.info(f"[CLAUDE] バッチ {batch_num}/{total_batches} 完了: {batch_elapsed:.1f}秒")
                results.extend(batch_results)
            except Exception as e:
                logger.error(f"[CLAUDE] バッチ {batch_num} 検証失敗: {type(e).__name__}: {e}")
                # エラー時は元のデータを返す（手動確認用）
                for clinic in batch:
                    results.append(
                        {
                            **clinic.model_dump(),
                            "is_valid": True,
                            "validation_reason": f"API error: {e}",
                        }
                    )

        total_elapsed = time.time() - start_time
        valid_count = sum(1 for r in results if r.get("is_valid", False))
        logger.info(f"[CLAUDE] 検証完了: {len(results)}件中 {valid_count}件有効 ({total_elapsed:.1f}秒)")

        return results

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((json.JSONDecodeError, anthropic.APIError)),
        reraise=True,
    )
    def _validate_batch_internal(
        self, clinics: list["Clinic"], start_index: int
    ) -> list[dict]:
        """バッチ検証の内部実装"""

        # クリニック情報をJSON形式で整形
        clinics_data = [
            {
                "index": i,
                "name": clinic.name,
                "url": clinic.url or "",
                "address": clinic.address or "",
            }
            for i, clinic in enumerate(clinics)
        ]
        clinics_json = json.dumps(clinics_data, ensure_ascii=False, indent=2)

        prompt = self.VALIDATION_PROMPT.format(clinics_json=clinics_json)

        logger.debug(f"Validating batch of {len(clinics)} clinics")

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        # レスポンスをパース
        response_text = response.content[0].text.strip()

        # JSONブロックを抽出（コードブロックで囲まれている場合に対応）
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                if line.startswith("```") and in_json:
                    break
                if in_json:
                    json_lines.append(line)
            response_text = "\n".join(json_lines)

        validations = json.loads(response_text)

        # デバッグ: Claude APIのレスポンスをログ出力
        logger.info(f"Claude API response: {json.dumps(validations, ensure_ascii=False)}")

        # 検証結果をクリニック情報にマージ
        results: list[dict] = []
        for validation in validations:
            idx = validation.get("index", 0)
            if idx < len(clinics):
                clinic = clinics[idx]
                clinic_dict = clinic.model_dump()

                clinic_dict["is_official_site"] = validation.get("is_official_site")
                clinic_dict["is_major_chain"] = validation.get("is_major_chain", False)
                clinic_dict["normalized_name"] = validation.get(
                    "normalized_name", clinic.name
                )
                clinic_dict["validation_reason"] = validation.get("reason", "")

                # 有効判定: 公式サイトかつ大手チェーンでない
                clinic_dict["is_valid"] = (
                    validation.get("is_official_site") is not False
                    and validation.get("is_major_chain") is False
                )

                results.append(clinic_dict)

        logger.info(f"Validated {len(results)} clinics")
        return results

    def validate_single(self, clinic: "Clinic") -> dict:
        """
        単一クリニックを検証

        Args:
            clinic: クリニック情報

        Returns:
            検証結果を含むクリニック情報（dict形式）
        """
        results = self.validate_batch([clinic])
        return results[0] if results else {**clinic.model_dump(), "is_valid": True}
