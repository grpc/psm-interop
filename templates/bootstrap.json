{
  "xds_servers": [
% for i, server in enumerate(servers):
    {
      "server_uri": "${server}",
      "channel_creds": [
        {
          "type": "insecure"
        }
      ],
      "server_features": [
        "xds_v3"
      ]
    }${','*bool(i < len(servers)-1)}
% endfor
  ],
  "authorities": {
    "traffic-director-c2p.xds.googleapis.com": {
      "xds_servers": [
        {
          "server_uri": "dns:///directpath-pa.googleapis.com",
          "channel_creds": [
            {
              "type": "google_default"
            }
          ],
          "server_features": [
            "xds_v3",
            "ignore_resource_deletion"
          ]
        }
      ],
      "client_listener_resource_name_template": "xdstp://traffic-director-c2p.xds.googleapis.com/envoy.config.listener.v3.Listener/%s"
    }
  },
  "node": {
    "id": "test-id",
    "cluster": "cluster",
    "metadata": {
      "INSTANCE_IP": "192.168.69.210",
      "TRAFFICDIRECTOR_GRPC_BOOTSTRAP_GENERATOR_SHA": "53bfefe9062967c4ab3df9d9a687929f4f764bf0"
    },
    "locality": {
      "zone": "us-west4-b"
    }
  },
  "certificate_providers": {
    "google_cloud_private_spiffe": {
      "plugin_name": "file_watcher",
      "config": {
        "certificate_file": "/var/run/secrets/workload-spiffe-credentials/certificates.pem",
        "private_key_file": "/var/run/secrets/workload-spiffe-credentials/private_key.pem",
        "ca_certificate_file": "/var/run/secrets/workload-spiffe-credentials/ca_certificates.pem",
        "refresh_interval": "600s"
      }
    }
  },
  "server_listener_resource_name_template": "grpc/server?xds.resource.listening_address=%s"
}
