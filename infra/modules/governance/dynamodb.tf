# infra/modules/governance/dynamodb.tf
# All 7 DynamoDB tables required by the Data Meshy governance plane.
# All tables use PAY_PER_REQUEST billing and SSE with AWS-managed KMS.

###############################################################################
# mesh-domains
###############################################################################
resource "aws_dynamodb_table" "mesh_domains" {
  name         = "mesh-domains"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "domain_name"

  attribute {
    name = "domain_name"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-domains"
  })
}

###############################################################################
# mesh-products
###############################################################################
resource "aws_dynamodb_table" "mesh_products" {
  name         = "mesh-products"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "domain#product_name"

  attribute {
    name = "domain#product_name"
    type = "S"
  }

  attribute {
    name = "tag"
    type = "S"
  }

  attribute {
    name = "classification"
    type = "S"
  }

  attribute {
    name = "domain"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1-tag"
    hash_key        = "tag"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "GSI2-classification"
    hash_key        = "classification"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "GSI3-domain"
    hash_key        = "domain"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-products"
  })
}

###############################################################################
# mesh-subscriptions
###############################################################################
resource "aws_dynamodb_table" "mesh_subscriptions" {
  name         = "mesh-subscriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "product_id"
  range_key    = "subscriber_account_id"

  attribute {
    name = "product_id"
    type = "S"
  }

  attribute {
    name = "subscriber_account_id"
    type = "S"
  }

  attribute {
    name = "subscriber_domain"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1-subscriber_domain"
    hash_key        = "subscriber_domain"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-subscriptions"
  })
}

###############################################################################
# mesh-quality-scores
###############################################################################
resource "aws_dynamodb_table" "mesh_quality_scores" {
  name         = "mesh-quality-scores"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "product_id"
  range_key    = "timestamp"

  attribute {
    name = "product_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-quality-scores"
  })
}

###############################################################################
# mesh-audit-log  (PITR enabled; append-only by role policy)
###############################################################################
resource "aws_dynamodb_table" "mesh_audit_log" {
  name         = "mesh-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  range_key    = "timestamp"

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  attribute {
    name = "domain"
    type = "S"
  }

  attribute {
    name = "event_type"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1-domain"
    hash_key        = "domain"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "GSI2-event_type"
    hash_key        = "event_type"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-audit-log"
  })
}

###############################################################################
# mesh-event-dedup  (TTL: 24 hours via expires_at attribute)
###############################################################################
resource "aws_dynamodb_table" "mesh_event_dedup" {
  name         = "mesh-event-dedup"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-event-dedup"
  })
}

###############################################################################
# mesh-pipeline-locks  (TTL: configurable via expires_at attribute)
###############################################################################
resource "aws_dynamodb_table" "mesh_pipeline_locks" {
  name         = "mesh-pipeline-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "product_id"
  range_key    = "lock_key"

  attribute {
    name = "product_id"
    type = "S"
  }

  attribute {
    name = "lock_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-pipeline-locks"
  })
}
