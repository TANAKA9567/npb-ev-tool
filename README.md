# NPB期待値ラボ

Pinnacleのマネーライン、±1.5、オーバー／アンダーを統合し、9回終了時点の得点分布からハンデ別の期待値を計算するStreamlitアプリです。

## Streamlit Community Cloudへ公開

1. このフォルダー内のファイルをすべてGitHubリポジトリの最上位へアップロードします。
2. Streamlit Community Cloudで対象リポジトリと`main`ブランチを選択します。
3. Main file pathへ`app2.py`を指定します。
4. Deployを押します。

## ローカル起動

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
python -m streamlit run app2.py
```

## 主な機能

- Pinnacle公開APIからNPBオッズを取得
- 12球団のチームロゴ表示
- 出しチームとハンデをプルダウン入力
- ML・±1.5・O/Uを使った3市場統一得点分布
- 9回勝率、同点率、点差別確率、EVを計算
- EVに応じた見送り・小・中・大の資金配分

## 注意

- 本ツールは計算補助であり、利益を保証するものではありません。
- Pinnacle側の仕様変更やアクセス状況により、自動取得できない場合があります。
- 球団ロゴは各権利者の商標であり、個人利用を前提としています。
