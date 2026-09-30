package main

import (
	"bytes"
	"context"
	"encoding/json"
	pb "example.com/project-grpc/api"
	"fmt"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/emptypb"
	"io"
	"net"
	"net/http"
	"time"
)

type caller struct {
	transport string
	address   string
	http      *http.Client
	conn      *grpc.ClientConn
	users     pb.UserServiceClient
	hr        pb.HRServiceClient
}

func newCaller(transport, address string, concurrency int) (*caller, error) {
	c := &caller{transport: transport, address: address}
	if transport == "rest" {
		c.http = &http.Client{Transport: &http.Transport{
			Proxy: nil, MaxIdleConns: concurrency, MaxIdleConnsPerHost: concurrency, MaxConnsPerHost: concurrency,
			IdleConnTimeout: 90 * time.Second, DialContext: (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		}}
	} else if transport == "grpc" {
		conn, err := grpc.NewClient(address, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			return nil, err
		}
		c.conn = conn
		c.users = pb.NewUserServiceClient(conn)
		c.hr = pb.NewHRServiceClient(conn)
	} else {
		return nil, fmt.Errorf("unknown transport %q", transport)
	}
	return c, nil
}
func (c *caller) Close() {
	if c.conn != nil {
		_ = c.conn.Close()
	}
	if c.http != nil {
		c.http.CloseIdleConnections()
	}
}
func (c *caller) invoke(ctx context.Context, o Operation) (any, error) {
	if c.transport == "rest" {
		return c.invokeHTTP(ctx, o)
	}
	if o.Resource == "users" {
		in := &pb.UserInput{Name: o.User.Name, Email: o.User.Email, Address: o.User.Address}
		switch o.Action {
		case "list":
			return c.users.ListUsers(ctx, &emptypb.Empty{})
		case "get":
			return c.users.GetUser(ctx, &pb.UserID{Id: o.ID})
		case "create":
			return c.users.CreateUser(ctx, in)
		case "update":
			return c.users.UpdateUser(ctx, &pb.UpdateUserRequest{Id: o.ID, User: in})
		case "delete":
			return c.users.DeleteUser(ctx, &pb.UserID{Id: o.ID})
		}
	} else {
		in := &pb.LeaveInput{UserId: o.Leave.UserID, StartDate: o.Leave.StartDate, EndDate: o.Leave.EndDate}
		switch o.Action {
		case "list":
			return c.hr.ListLeaves(ctx, &emptypb.Empty{})
		case "get":
			return c.hr.GetLeave(ctx, &pb.LeaveID{Id: o.ID})
		case "create":
			return c.hr.CreateLeave(ctx, in)
		case "update":
			return c.hr.UpdateLeave(ctx, &pb.UpdateLeaveRequest{Id: o.ID, Leave: in})
		case "delete":
			return c.hr.DeleteLeave(ctx, &pb.LeaveID{Id: o.ID})
		}
	}
	return nil, fmt.Errorf("unknown operation %s", o.Name())
}
func (c *caller) invokeHTTP(ctx context.Context, o Operation) (any, error) {
	method := map[string]string{"list": "GET", "get": "GET", "create": "POST", "update": "PUT", "delete": "DELETE"}[o.Action]
	path := "http://" + c.address + "/" + o.Resource
	if o.Action == "get" || o.Action == "update" || o.Action == "delete" {
		path += fmt.Sprintf("/%d", o.ID)
	}
	var payload []byte
	var err error
	if o.Action == "create" || o.Action == "update" {
		if o.Resource == "users" {
			payload, err = json.Marshal(struct {
				Name    string `json:"name"`
				Email   string `json:"email"`
				Address string `json:"address"`
			}{o.User.Name, o.User.Email, o.User.Address})
		} else {
			payload, err = json.Marshal(struct {
				UserID    int64  `json:"user_id"`
				StartDate string `json:"start_date"`
				EndDate   string `json:"end_date"`
			}{o.Leave.UserID, o.Leave.StartDate, o.Leave.EndDate})
		}
		if err != nil {
			return nil, err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, path, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	want := 200
	if o.Action == "create" {
		want = 201
	}
	if o.Action == "delete" {
		want = 204
	}
	if resp.StatusCode != want {
		return nil, fmt.Errorf("HTTP %d, want %d: %s", resp.StatusCode, want, body)
	}
	if o.Action == "delete" {
		if len(body) != 0 {
			return nil, fmt.Errorf("nonempty delete response")
		}
		return nil, nil
	}
	if o.Resource == "users" {
		if o.Action == "list" {
			var v []User
			err = json.Unmarshal(body, &v)
			return v, err
		}
		var v User
		err = json.Unmarshal(body, &v)
		return v, err
	}
	if o.Action == "list" {
		var v []Leave
		err = json.Unmarshal(body, &v)
		return v, err
	}
	var v Leave
	err = json.Unmarshal(body, &v)
	return v, err
}
