output "function_id" {
  description = "ID of the watcher function (needed to set secret environment variables)."
  value       = scaleway_function.watcher.id
}

output "namespace_id" {
  description = "ID of the function namespace."
  value       = scaleway_function_namespace.watcher.id
}

output "schedule" {
  description = "CRON schedule (UTC) of the watcher."
  value       = scaleway_function_cron.watcher.schedule
}

output "tem_dns_records" {
  description = "DNS records to publish for the TEM sender domain (null when TEM is not managed here)."
  value = local.tem_domain == "" ? null : {
    domain = scaleway_tem_domain.sender[0].name
    spf    = scaleway_tem_domain.sender[0].spf_config
    dkim   = scaleway_tem_domain.sender[0].dkim_config
  }
}
