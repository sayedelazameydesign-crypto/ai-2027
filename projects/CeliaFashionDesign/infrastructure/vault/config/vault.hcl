ui = true

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr = "http://vault:8200"
cluster_addr = "https://vault:8201"

# Disable mlock for containers
disable_mlock = true

# Log level
log_level = "Info"

# Plugin directory
plugin_directory = "/vault/plugins"
