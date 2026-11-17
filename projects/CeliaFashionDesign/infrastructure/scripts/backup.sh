#!/bin/bash
set -e

BACKUP_DIR="/opt/celia/backups/$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/opt/celia/backups/backup.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting backup..."

# Create backup directory
mkdir -p "$BACKUP_DIR"

# 1. Backup Database
log "Backing up PostgreSQL database..."
docker exec postgres pg_dump -U celia celia_db | gzip > "$BACKUP_DIR/database.sql.gz"

# 2. Backup Qdrant Vector DB
log "Backing up Qdrant vector database..."
docker exec qdrant tar czf - /qdrant/storage > "$BACKUP_DIR/qdrant_data.tar.gz"

# 3. Backup Directus Uploads
log "Backing up Directus uploads..."
docker cp directus:/directus/uploads "$BACKUP_DIR/uploads"

# 4. Backup Redis (if needed)
log "Backing up Redis..."
docker exec redis redis-cli SAVE
docker cp redis:/data/dump.rdb "$BACKUP_DIR/redis_dump.rdb"

# 5. Backup Configuration Files
log "Backing up configuration files..."
cp /opt/celia/infrastructure/.env "$BACKUP_DIR/.env.backup"
cp /opt/celia/infrastructure/docker-compose.yml "$BACKUP_DIR/"

# 6. Create compressed archive
log "Creating compressed archive..."
cd "$(dirname "$BACKUP_DIR")"
tar czf "${BACKUP_DIR}.tar.gz" "$(basename "$BACKUP_DIR")"
rm -rf "$BACKUP_DIR"

# 7. Optional: Upload to remote storage (uncomment if using rclone)
# log "Uploading to remote storage..."
# rclone copy "${BACKUP_DIR}.tar.gz" remote:celia-backups/

# 8. Cleanup old backups (keep last 7 days)
log "Cleaning up old backups..."
find /opt/celia/backups -name "*.tar.gz" -type f -mtime +7 -delete

log "Backup completed successfully: ${BACKUP_DIR}.tar.gz"
log "Backup size: $(du -h "${BACKUP_DIR}.tar.gz" | cut -f1)"
