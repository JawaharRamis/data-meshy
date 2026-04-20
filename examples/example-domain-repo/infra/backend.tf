terraform {
  backend "s3" {
    bucket         = "REPLACE_ME-terraform-state"
    key            = "sales/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE_ME-terraform-locks"
    encrypt        = true
  }
}
