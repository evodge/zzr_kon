#!/bin/bash
# 1. 確実にデータのフォルダに移動
cd /home/evodge-kon/zzr_kon/home_temp

# 2. data.json と、このスクリプト自身(git_push.sh)を両方ともGitに登録
git add data.json git_push.sh

# 3. 変更があればコミット（記録）する
git commit -m "Update sensor data: $(date '+%Y-%m-%d %H:%M:%S')"

# 4. GitHubへ送信
git push origin master