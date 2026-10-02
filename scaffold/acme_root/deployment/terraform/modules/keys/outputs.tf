output "alias_name" {
  description = "The process reads it as ACME_KMS_KEY_ID: the account's key for this environment."
  value       = "alias/acme-${var.environment}-sessions"
}

output "policy_arn" {
  description = "Attached to every task role that seals or opens a session's content."
  value       = aws_iam_policy.use.arn
}
