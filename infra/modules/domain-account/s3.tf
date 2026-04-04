##############################################################################
# domain-account/s3.tf
#
# Three S3 buckets per domain: raw (Bronze), silver (Validated), gold (Product).
#
# Security model:
#   - All buckets: SSE-KMS with domain CMK, bucket_key_enabled=true,
#     versioning enabled, public access block, HTTPS-only deny, OrgID deny.
#   - Raw + Silver: deny ALL cross-account access.
#   - Gold: deny s3:GetObject to ALL principals EXCEPT GlueJobExecutionRole
#     and lakeformation service-linked role (LF bypass prevention).
#   - Raw: lifecycle — Glacier after 90 days, expire after 365 days.
##############################################################################

##############################################################################
# RAW BUCKET
##############################################################################

resource "aws_s3_bucket" "raw" {
  bucket = "${var.domain}-raw-${local.account_id}"
  tags   = merge(local.tags, { Layer = "raw" })
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.domain.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "raw-glacier-expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # HTTPS-only
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
      # Deny requests from outside the Organization
      {
        Sid       = "DenyNonOrgAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*"
        ]
        Condition = {
          StringNotEquals = { "aws:PrincipalOrgID" = var.aws_org_id }
        }
      },
      # Deny ALL cross-account access (raw is internal to domain only)
      {
        Sid       = "DenyAllCrossAccountAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*"
        ]
        Condition = {
          StringNotEquals = { "aws:PrincipalAccount" = local.account_id }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.raw]
}

##############################################################################
# SILVER BUCKET
##############################################################################

resource "aws_s3_bucket" "silver" {
  bucket = "${var.domain}-silver-${local.account_id}"
  tags   = merge(local.tags, { Layer = "silver" })
}

resource "aws_s3_bucket_versioning" "silver" {
  bucket = aws_s3_bucket.silver.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.domain.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "silver" {
  bucket                  = aws_s3_bucket.silver.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "silver" {
  bucket = aws_s3_bucket.silver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # HTTPS-only
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
      # Deny requests from outside the Organization
      {
        Sid       = "DenyNonOrgAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*"
        ]
        Condition = {
          StringNotEquals = { "aws:PrincipalOrgID" = var.aws_org_id }
        }
      },
      # Deny ALL cross-account access (silver is internal to domain only)
      {
        Sid       = "DenyAllCrossAccountAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*"
        ]
        Condition = {
          StringNotEquals = { "aws:PrincipalAccount" = local.account_id }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.silver]
}

##############################################################################
# GOLD BUCKET — stricter policy to prevent Lake Formation bypass
##############################################################################

resource "aws_s3_bucket" "gold" {
  bucket = "${var.domain}-gold-${local.account_id}"
  tags   = merge(local.tags, { Layer = "gold" })
}

resource "aws_s3_bucket_versioning" "gold" {
  bucket = aws_s3_bucket.gold.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.domain.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "gold" {
  bucket                  = aws_s3_bucket.gold.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "gold" {
  bucket = aws_s3_bucket.gold.id

  # CRITICAL: This policy prevents Lake Formation bypass.
  # All GetObject requests are denied EXCEPT from:
  #   1. GlueJobExecutionRole (ETL jobs write/read gold data)
  #   2. Lake Formation service-linked role (serves cross-account consumers)
  # This means no IAM role — even with s3:GetObject on the bucket — can
  # bypass Lake Formation and read gold data directly.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # LF-bypass prevention: deny GetObject to all except approved principals
      {
        Sid       = "DenyDirectGetObjectExceptLFAndGlue"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        Condition = {
          StringNotLike = {
            "aws:PrincipalArn" = [
              "arn:aws:iam::${local.account_id}:role/GlueJobExecutionRole",
              "arn:aws:iam::${local.account_id}:role/aws-service-role/lakeformation.amazonaws.com/*"
            ]
          }
        }
      },
      # HTTPS-only
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
      # Deny requests from outside the Organization
      {
        Sid       = "DenyNonOrgAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
        Condition = {
          StringNotEquals = { "aws:PrincipalOrgID" = var.aws_org_id }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.gold]
}
