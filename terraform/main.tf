provider "scaleway" {
  project_id = var.project_id
  region     = var.region
}

locals {
  # Window boundaries are aligned to 00:00 UTC; fire a few minutes after each.
  schedule   = "${var.schedule_minute} */${var.interval_hours} * * *"
  tem_domain = trimspace(var.tem_domain)

  config_interval = try(jsondecode(var.watcher_config).interval_hours, 6)
}

# Package src/ (handler.py + mailbox_watcher/) as the function archive.
data "archive_file" "function" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/.build/function.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}

resource "scaleway_function_namespace" "watcher" {
  name        = var.name
  description = "Mailbox watcher (managed by Terraform)"
}

resource "scaleway_function" "watcher" {
  namespace_id = scaleway_function_namespace.watcher.id
  name         = var.name
  runtime      = var.runtime
  handler      = "handler.handle"

  # Private: only the CRON trigger (or a holder of a function token) can invoke it.
  privacy = "private"

  min_scale    = 0
  max_scale    = 1
  memory_limit = var.memory_limit
  timeout      = var.timeout_seconds

  zip_file = data.archive_file.function.output_path
  zip_hash = data.archive_file.function.output_sha256
  deploy   = true

  environment_variables = {
    MAILBOX_WATCHER_CONFIG = var.watcher_config
    TEM_PROJECT_ID         = var.project_id
    TEM_REGION             = var.region
  }

  # IMAP passwords and the TEM secret key are set out of band (see README and
  # scripts/set-function-secret.sh) so they never pass through GitHub or the
  # Terraform state.
  lifecycle {
    ignore_changes = [secret_environment_variables]

    precondition {
      condition     = local.config_interval == var.interval_hours
      error_message = "interval_hours in watcher_config must equal the Terraform variable interval_hours."
    }
  }
}

resource "scaleway_function_cron" "watcher" {
  function_id = scaleway_function.watcher.id
  name        = "${var.name}-schedule"
  schedule    = local.schedule
  args        = jsonencode({})
}

resource "scaleway_tem_domain" "sender" {
  count = local.tem_domain == "" ? 0 : 1

  name       = local.tem_domain
  accept_tos = true
}
