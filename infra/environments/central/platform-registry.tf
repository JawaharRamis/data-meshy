###############################################################################
# Platform Module Registry — S3 bucket
#
# Domain repos reference TF modules via git:: sources (see README contracts).
# This bucket is a future-ready alternative: release-modules.yml zips and
# uploads versioned module archives here so domain teams can pin to s3:: sources
# without needing git access. Empty at deploy time; populated by CI on tag push.
###############################################################################

resource "aws_s3_bucket" "platform_modules" {
  bucket = "data-meshy-platform-modules-${data.aws_caller_identity.central.account_id}"

  tags = {
    Purpose = "platform-module-registry"
  }
}

# Block all public access — modules are internal platform assets.
resource "aws_s3_bucket_public_access_block" "platform_modules" {
  bucket = aws_s3_bucket.platform_modules.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning so old module zips are preserved.
resource "aws_s3_bucket_versioning" "platform_modules" {
  bucket = aws_s3_bucket.platform_modules.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt at rest using the central KMS key.
resource "aws_s3_bucket_server_side_encryption_configuration" "platform_modules" {
  bucket = aws_s3_bucket.platform_modules.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = module.governance.central_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

# Expire noncurrent module zip versions after 90 days to bound storage cost.
resource "aws_s3_bucket_lifecycle_configuration" "platform_modules" {
  bucket = aws_s3_bucket.platform_modules.id

  rule {
    id     = "expire-old-module-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "platform_modules_bucket_name" {
  description = "S3 bucket name for versioned platform module zip archives."
  value       = aws_s3_bucket.platform_modules.bucket
}
