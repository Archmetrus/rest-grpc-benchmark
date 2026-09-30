package main

import (
	pb "example.com/project-grpc/api"
	"fmt"
	"reflect"
)

type User struct {
	ID      int64  `json:"id"`
	Name    string `json:"name"`
	Email   string `json:"email"`
	Address string `json:"address"`
}
type Leave struct {
	ID        int64  `json:"id"`
	UserID    int64  `json:"user_id"`
	StartDate string `json:"start_date"`
	EndDate   string `json:"end_date"`
}
type Seed struct {
	Users  []User  `json:"users"`
	Leaves []Leave `json:"leaves"`
}
type Operation struct {
	Resource string `json:"resource"`
	Action   string `json:"action"`
	ID       int64  `json:"id"`
	User     User   `json:"user"`
	Leave    Leave  `json:"leave"`
}

func (o Operation) Name() string { return o.Resource + "." + o.Action }

const seedUsers int64 = 10000
const callsPerOperation int64 = 500
const responseBatchSize = 50

func seedData() Seed {
	s := Seed{}
	for i := int64(1); i <= seedUsers; i++ {
		s.Users = append(s.Users, User{i, fmt.Sprintf("User %04d", i), fmt.Sprintf("user%04d@example.com", i), fmt.Sprintf("Street %04d, Ankara", i)})
		s.Leaves = append(s.Leaves, Leave{2*i - 1, i, "2026-06-01", "2026-06-05"}, Leave{2 * i, i, "2026-08-10", "2026-08-14"})
	}
	return s
}
func workload() [][]Operation {
	var groups [][]Operation
	for _, r := range []string{"users", "leaves"} {
		for _, action := range []string{"list", "get", "create", "update", "delete"} {
			group := []Operation{}
			for i := int64(1); i <= callsPerOperation; i++ {
				o := Operation{Resource: r, Action: action, ID: i}
				if action == "update" {
					o.ID = i + callsPerOperation
				}
				if action == "delete" {
					o.ID = i + 2*callsPerOperation
				}
				if action == "create" || action == "update" {
					o.User = User{Name: fmt.Sprintf("%s User %03d", action, i), Email: fmt.Sprintf("%s%03d@example.com", action, i), Address: fmt.Sprintf("%s Street %03d, Istanbul", action, i)}
					o.Leave = Leave{UserID: i, StartDate: fmt.Sprintf("2027-01-%02d", (i-1)%28+1), EndDate: fmt.Sprintf("2027-02-%02d", (i-1)%28+1)}
				}
				group = append(group, o)
			}
			groups = append(groups, group)
		}
	}
	return groups
}

// Normalize outside the timed call: gRPC exposes generated messages, REST exposes JSON models.
func normalized(value any) any {
	switch v := value.(type) {
	case *pb.User:
		return User{v.Id, v.Name, v.Email, v.Address}
	case *pb.Leave:
		return Leave{v.Id, v.UserId, v.StartDate, v.EndDate}
	case *pb.UserList:
		a := []User{}
		for _, u := range v.Users {
			a = append(a, normalized(u).(User))
		}
		return a
	case *pb.LeaveList:
		a := []Leave{}
		for _, l := range v.Leaves {
			a = append(a, normalized(l).(Leave))
		}
		return a
	default:
		return value
	}
}
func validate(o Operation, value any, seed Seed) error {
	if o.Action == "delete" {
		return nil
	}
	value = normalized(value)
	var expected any
	if o.Resource == "users" {
		switch o.Action {
		case "list":
			expected = seed.Users
		case "get":
			expected = seed.Users[o.ID-1]
		default:
			got, ok := value.(User)
			if !ok {
				return fmt.Errorf("unexpected user type %T", value)
			}
			want := o.User
			if o.Action == "create" {
				if got.ID <= seedUsers {
					return fmt.Errorf("invalid generated user ID %d", got.ID)
				}
				want.ID = got.ID
			} else {
				want.ID = o.ID
			}
			expected = want
		}
	} else {
		switch o.Action {
		case "list":
			expected = seed.Leaves
		case "get":
			expected = seed.Leaves[o.ID-1]
		default:
			got, ok := value.(Leave)
			if !ok {
				return fmt.Errorf("unexpected leave type %T", value)
			}
			want := o.Leave
			if o.Action == "create" {
				if got.ID <= 2*seedUsers {
					return fmt.Errorf("invalid generated leave ID %d", got.ID)
				}
				want.ID = got.ID
			} else {
				want.ID = o.ID
			}
			expected = want
		}
	}
	if !reflect.DeepEqual(value, expected) {
		return fmt.Errorf("response content mismatch for %s id=%d", o.Name(), o.ID)
	}
	return nil
}
