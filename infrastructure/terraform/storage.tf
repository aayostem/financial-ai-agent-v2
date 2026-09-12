# =============================================================================
# storage.tf
# Three buckets, declared directly here -- not wrapped in their own module.
# There's exactly one caller of each; a module buys nothing until there's a
# second one.
# =============================================================================

locals {
  buckets = {
    documents   = "${var.project_name}-documents"
    filings     = "${var.project_name}-filings"
    access_logs = "${var.project_name}-access-logs"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets
  bucket   = each.value
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# THE FIX for the flagged missing-lifecycle-policy issue. documents/filings
# move to cheaper storage automatically as they age instead of accumulating
# indefinitely at Standard-tier cost; access_logs -- genuinely disposable --
# expires outright after 90 days rather than growing forever.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    id     = "intelligent-tiering"
    status = "Enabled"

    dynamic "transition" {
      for_each = each.key == "access_logs" ? [] : [1]
      content {
        days          = 0
        storage_class = "INTELLIGENT_TIERING"
      }
    }

    dynamic "expiration" {
      for_each = each.key == "access_logs" ? [1] : []
      content {
        days = 90
      }
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# Kept exactly as flagged working-well: deny any request not using TLS.
resource "aws_s3_bucket_policy" "tls_only" {
  for_each = local.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.this[each.key].arn,
        "${aws_s3_bucket.this[each.key].arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}
