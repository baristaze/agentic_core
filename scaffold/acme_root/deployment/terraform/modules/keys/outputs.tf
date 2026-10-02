output "alias_name" {
  description = "The process reads it as ACME_KMS_KEY_ID."
  value       = aws_kms_alias.sessions.name
}

output "policy_arn" {
  description = "Attached to every task role that seals or opens a session's content."
  value       = aws_iam_policy.use.arn
}
