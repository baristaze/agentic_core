variable "environment" {
  description = "Environment name."
  type        = string
}

variable "destroyable" {
  description = "True on the nuke's way down only: the key waits the shortest deletion window KMS allows."
  type        = bool
}
