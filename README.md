# Concierge

Discord（#concierge）を窓口にした専属秘書。Recall が記録したもの（Obsidian）と、メール・天気などを集めて、
Codex がまとめて知らせる。

## 構想

```
材料:  Recall（タスク・日記・やりたいこと, Nextcloud 経由で読むだけ）
       メール（受け皿 Gmail に4アドレスを転送 → Gmail API 読み取り専用）
       天気（Open-Meteo）
  ↓
pk-206 の Concierge bot（Recall とは別アプリ・別プロセス）→ Codex でまとめる
  ↓
Discord #concierge: 毎朝のモーニングブリーフ / 返信が要るメール / 締切のあるメール …
```

- Gmail の鍵（token.json）を持つのは Concierge だけ。スコープは `gmail.readonly`（読むだけ。送信・削除はできない）
- メール本体の正は各受信箱。受け皿はAIが読むためのコピー（転送が漏れても元の受信箱には残る）
- 受け皿: 4アドレス（outlook.jp / 大学 / 就活 Gmail / Google メイン）から転送

## 進め方

1. [x] 各アドレス → 受け皿 Gmail への転送
2. [ ] Google Cloud: Gmail API + OAuth（公開ステータスを「本番」にして、7日でログインが切れるのを防ぐ）
3. [ ] `tools/gmail_auth.py`（Mac）で token.json を作り、pk-206 の `~/.config/concierge/` へ
4. [ ] `tools/gmail_test.py`（pk-206）で読めるか確認
5. [ ] Discord アプリ「Concierge」と #concierge
6. [ ] モーニングブリーフを Recall から移し、メールの要約を足す

## pk-206 側（リポジトリ外）

- `~/.config/concierge/`（700）: `client_secret_….json`, `token.json`（600）
