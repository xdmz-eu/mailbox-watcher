variable "project_id" {
  description = "Scaleway project ID that hosts the watcher (also used for TEM)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.project_id))
    error_message = "project_id must be a Scaleway project UUID."
  }
}

variable "region" {
  description = "Scaleway region for the function and TEM."
  type        = string
  default     = "fr-par"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]{3}$", var.region))
    error_message = "region must look like 'fr-par'."
  }
}

variable "name" {
  description = "Name of the function namespace and function."
  type        = string
  default     = "mailbox-watcher"

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,32}[a-z0-9])?$", var.name))
    error_message = "name must be lowercase letters, digits and hyphens."
  }
}

variable "runtime" {
  description = "Scaleway Python runtime identifier."
  type        = string
  default     = "python312"
}

variable "interval_hours" {
  description = "Polling interval. Must match interval_hours in watcher_config and divide 24."
  type        = number
  default     = 6

  validation {
    condition     = contains([1, 2, 3, 4, 6, 8, 12, 24], var.interval_hours)
    error_message = "interval_hours must divide 24 (1, 2, 3, 4, 6, 8, 12 or 24)."
  }
}

variable "schedule_minute" {
  description = "Minute after each interval boundary at which the CRON trigger fires."
  type        = number
  default     = 5

  validation {
    condition     = var.schedule_minute >= 0 && var.schedule_minute <= 59 && floor(var.schedule_minute) == var.schedule_minute
    error_message = "schedule_minute must be an integer between 0 and 59."
  }
}

variable "memory_limit" {
  description = "Function memory in MB."
  type        = number
  default     = 128
}

variable "timeout_seconds" {
  description = "Function timeout in seconds."
  type        = number
  default     = 300
}

variable "watcher_config" {
  description = "Watcher JSON configuration (no passwords). Provided via TF_VAR_watcher_config; never committed."
  type        = string
  sensitive   = true

  validation {
    condition     = can(jsondecode(var.watcher_config).mailboxes)
    error_message = "watcher_config must be a JSON document with a 'mailboxes' list."
  }
}

variable "tem_domain" {
  description = "Sender domain to register in Transactional Email. Empty string skips TEM domain management."
  type        = string
  default     = ""
}
