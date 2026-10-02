# The use of the key every session's data keys are wrapped under. The key
# itself is the account's (the `account` module), so destroying this
# environment never reaches it. The grant is on the keys tagged as this
# environment's, so a version wrapped under an earlier key of the
# environment still opens while that key exists. The use is the task
# roles' alone: a database login holds the wrapped keys and cannot unwrap
# one, and neither can the investigate role, which reads the database.

data "aws_partition" "current" {}
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  tags = { "acme:environment" = var.environment }
}

data "aws_iam_policy_document" "use" {
  statement {
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:kms:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:key/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/acme:environment"
      values   = [var.environment]
    }
  }
}

resource "aws_iam_policy" "use" {
  name   = "acme-${var.environment}-session-keys"
  policy = data.aws_iam_policy_document.use.json
  tags   = local.tags
}
