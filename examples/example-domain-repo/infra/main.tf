terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

module "domain_account" {
  source                = "git::https://github.com/JawaharRamis/data-meshy-platform.git//infra/modules/domain-account?ref=v1.0.0"
  domain                = var.domain
  account_id            = var.account_id
  owner                 = var.owner
  central_event_bus_arn = var.central_event_bus_arn
}

module "data_product" {
  source       = "git::https://github.com/JawaharRamis/data-meshy-platform.git//infra/modules/data-product?ref=v1.0.0"
  domain       = var.domain
  product_name = "customer_orders"
  owner        = var.owner
  account_id   = var.account_id
  aws_region   = var.aws_region

  # Values populated from domain_account module outputs
  raw_bucket_arn    = module.domain_account.raw_bucket_arn
  silver_bucket_arn = module.domain_account.silver_bucket_arn
  gold_bucket_arn   = module.domain_account.gold_bucket_arn
  kms_key_arn       = module.domain_account.kms_key_arn
  pipeline_role_arn = module.domain_account.pipeline_role_arn

  depends_on = [module.domain_account]
}
