docker build -t bot-gce .
docker tag bot-gce gcr.io/{project}/{repo}
docker push gcr.io/{project}/{repo}

docker run --env DISCORD_BOT_TOKEN= --env ALLOWED_ROLE_NAME= --env BACKUP_DISK_PATH= bot-gce