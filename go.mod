module example.com/microservice-benchmark

go 1.23.0

require (
	example.com/project-grpc v0.0.0
	google.golang.org/grpc v1.72.2
	google.golang.org/protobuf v1.36.6
)

require (
	golang.org/x/net v0.40.0 // indirect
	golang.org/x/sys v0.33.0 // indirect
	golang.org/x/text v0.25.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20250324211829-b45e905df463 // indirect
)

replace example.com/project-grpc => ../project-grpc
