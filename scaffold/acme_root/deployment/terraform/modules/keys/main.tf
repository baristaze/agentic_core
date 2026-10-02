# The KMS key every session's data keys are wrapped under, in this
# environment. A data key is generated, unwrapped, and re-wrapped under it
# with an encryption context of ids (the tenant, the session, the version),
# so one tenant's wrapped key opens nothing for another, and KMS's own trail
# records ids and never content. KMS rotates the key's material once a
# year; a rotation re-encrypts nothing it wrapped, and the engine re-wraps
# each data key in place.
#
# The use of the key is the task roles' alone, by the policy below. A
# database login holds the wrapped keys and cannot unwrap one, and neither
# can the investigate role, which reads the database.

locals {
  tags = { "acme:environment" = var.environment }
}

resource "aws_kms_key" "sessions" {
  description             = "Wraps the session keys of ${var.environment}."
  key_usage               = "ENCRYPT_DECRYPT"
  enable_key_rotation     = true
  deletion_window_in_days = var.destroyable ? 7 : 30
  tags                    = local.tags
}

resource "aws_kms_alias" "sessions" {
  name          = "alias/acme-${var.environment}-sessions"
  target_key_id = aws_kms_key.sessions.key_id
}

data "aws_iam_policy_document" "use" {
  statement {
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
    ]
    resources = [aws_kms_key.sessions.arn]
  }
}

resource "aws_iam_policy" "use" {
  name   = "acme-${var.environment}-session-keys"
  policy = data.aws_iam_policy_document.use.json
  tags   = local.tags
}
