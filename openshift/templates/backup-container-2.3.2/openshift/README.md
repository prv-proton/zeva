## Setup Backup container,use Test as example
1. Build patroni-backup image
oc -n e52f12-tools process -f ./templates/backup/backup-build.yaml \
-p NAME=patroni-backup GIT_REPO_URL=https://github.com/BCDevOps/backup-container.git GIT_REF=2.3.2 OUTPUT_IMAGE_TAG=2.3.2  \
| oc -n e52f12-tools apply -f -

2. Tag the backup-container image from tools project to environment project
oc tag e52f12-tools/patroni-backup:2.3.2 e52f12-test/patroni-backup:test

3. add to ./config/backup.conf, 9pm run backup, 10pm run verification
postgres=patroni-master-test:5432/zeva
0 1 * * * default ./backup.sh -s
0 7 * * * default ./backup.sh -s
0 13 * * * default ./backup.sh -s
0 19 * * * default ./backup.sh -s
0 22 * * * default ./backup.sh -s -v all
0 23 * * * default  find /backups/minio-backup/* -type d -ctime +7 | xargs rm -rf;mkdir -p /backups/minio-backup/$(date +%Y%m%d);cp -rp /minio-data/* /backups/minio-backup/$(date +%Y%m%d)

4. create deployment config for backup container
4.1 for test
oc -n e52f12-test process -f ./backup-deploy-test.yaml \
  -p NAME=patroni-backup \
  -p SOURCE_IMAGE_NAME=patroni-backup \
  -p IMAGE_NAMESPACE=e52f12-test \
  -p TAG_NAME=test \
  -p TABLE_SCHEMA=public \
  -p BACKUP_STRATEGY=rolling \
  -p ENVIRONMENT_FRIENDLY_NAME='ZEVA Database Backup' \
  -p ENVIRONMENT_NAME=e52f12-test \
  -p BACKUP_DIR=/backups/patroni-backup/ \
  -p DAILY_BACKUPS=5 \
  -p WEEKLY_BACKUPS=2 \
  -p MONTHLY_BACKUPS=1 \
  -p CONFIG_MAP_NAME=backup-conf \
  -p CONFIG_MOUNT_PATH=/ \
  -p BACKUP_VOLUME_NAME=backup-zeva-test \
  -p VERIFICATION_VOLUME_NAME=backup-verification \
  -p VERIFICATION_VOLUME_MOUNT_PATH=/var/lib/pgsql/data \
  -p MINIO_DATA_VOLUME_NAME=zeva-minio-test | \
  oc apply -f - -n e52f12-test

4.2 for production
oc -n e52f12-prod process -f ./backup-deploy-prod.yaml \
  -p NAME=patroni-backup \
  -p SOURCE_IMAGE_NAME=patroni-backup \
  -p IMAGE_NAMESPACE=e52f12-prod \
  -p TAG_NAME=prod \
  -p TABLE_SCHEMA=public \
  -p BACKUP_STRATEGY=rolling \
  -p ENVIRONMENT_FRIENDLY_NAME='ZEVA Database Backup' \
  -p ENVIRONMENT_NAME=e52f12-prod \
  -p BACKUP_DIR=/backups/patroni-backup/ \
  -p DAILY_BACKUPS=14 \
  -p WEEKLY_BACKUPS=4 \
  -p MONTHLY_BACKUPS=2 \
  -p CONFIG_MAP_NAME=backup-conf \
  -p CONFIG_MOUNT_PATH=/ \
  -p BACKUP_VOLUME_NAME=backup-zeva-prod \
  -p VERIFICATION_VOLUME_NAME=backup-verification \
  -p VERIFICATION_VOLUME_MOUNT_PATH=/var/lib/pgsql/data \
  -p MINIO_DATA_VOLUME_NAME=zeva-minio-prod | \
  oc apply -f - -n e52f12-prod