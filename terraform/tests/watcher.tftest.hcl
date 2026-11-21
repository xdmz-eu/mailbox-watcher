# Offline plan tests. Run with:
#   terraform -chdir=terraform init -backend=false
#   terraform -chdir=terraform test

mock_provider "scaleway" {}

variables {
  project_id     = "11111111-2222-3333-4444-555555555555"
  watcher_config = "{\"interval_hours\": 6, \"mailboxes\": []}"
}

run "defaults" {
  command = plan

  assert {
    condition     = scaleway_function_cron.watcher.schedule == "5 */6 * * *"
    error_message = "Default schedule must fire five minutes after each 6-hour boundary."
  }

  assert {
    condition     = scaleway_function.watcher.privacy == "private"
    error_message = "The function must not be publicly invocable."
  }

  assert {
    condition     = scaleway_function.watcher.handler == "handler.handle"
    error_message = "Handler must point at src/handler.py."
  }

  assert {
    condition     = scaleway_function.watcher.max_scale == 1
    error_message = "Only one instance may run at a time."
  }

  assert {
    condition     = length(scaleway_tem_domain.sender) == 0
    error_message = "No TEM domain is managed unless tem_domain is set."
  }

  assert {
    condition     = !contains(keys(scaleway_function.watcher.environment_variables), "TEM_SECRET_KEY")
    error_message = "Secrets must not be plain environment variables."
  }
}

run "custom_interval" {
  command = plan

  variables {
    interval_hours  = 12
    schedule_minute = 10
    watcher_config  = "{\"interval_hours\": 12, \"mailboxes\": []}"
  }

  assert {
    condition     = scaleway_function_cron.watcher.schedule == "10 */12 * * *"
    error_message = "Schedule must follow interval_hours and schedule_minute."
  }
}

run "tem_domain_enabled" {
  command = plan

  variables {
    tem_domain = "notifications.example.net"
  }

  assert {
    condition     = length(scaleway_tem_domain.sender) == 1
    error_message = "TEM domain must be created when tem_domain is set."
  }
}

run "interval_mismatch_rejected" {
  command = plan

  variables {
    interval_hours = 12
  }

  expect_failures = [scaleway_function.watcher]
}

run "invalid_interval_rejected" {
  command = plan

  variables {
    interval_hours = 5
  }

  expect_failures = [var.interval_hours]
}

run "invalid_config_rejected" {
  command = plan

  variables {
    watcher_config = "not json"
  }

  expect_failures = [var.watcher_config]
}
