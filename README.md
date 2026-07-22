# NPB スポーツベット期待値ツール

## 起動方法（VS Code）

VS Codeでこのフォルダーを開き、ターミナルで次を実行します。

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
streamlit run app.py
```

ブラウザが自動で開きます。別サイトの文章を貼り付け、Pinnacleのオッズを表で確認・入力してから「期待値を計算」を押してください。

## 画像文字認識

画像からの自動抽出には、Pythonパッケージとは別に Tesseract OCR と日本語言語データが必要です。
利用できない環境では、画像を見ながら表へ直接入力できます。OCR結果は誤ることがあるため、計算前に必ず確認してください。

次のコマンドで `jpn` と `eng` が表示されることを確認してください。

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

`jpn` がない場合、Tesseractのインストーラーを再実行し、追加言語の Japanese を選択してください。

## 注意

ハンデ1.3以上では得点差別の確率が必要です。画面左の点差配分は仮定であり、Pinnacleのマネーラインから直接得られる値ではありません。
Pinnacleのランライン（-1.5）からマージンを除去した「出し側が2点差以上で勝つ確率」が分かる場合は、表の専用列へ入力してください。
本ツールは計算補助で、利益を保証するものではありません。

## Streamlit Community Cloudで公開

このフォルダー一式をGitHubへ登録し、Streamlit Community Cloudで `app.py` を指定します。
Python 3.12を推奨します。`packages.txt`により、クラウド側にもTesseractと日本語OCRデータが導入されます。
