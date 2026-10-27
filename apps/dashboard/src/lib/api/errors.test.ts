import { describe, expect, it } from "vitest";
import { ApiRequestError, describeError, parseErrorEnvelope } from "./errors";

describe("parseErrorEnvelope", () => {
  it("reads the {error:{code,message,details}} envelope", () => {
    const err = parseErrorEnvelope(422, {
      error: {
        code: "UNSUPPORTED_DESTINATION",
        message: "no electronic path",
        details: { request_id: "r-1", destination: "fcc" },
      },
    });
    expect(err).toBeInstanceOf(ApiRequestError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("UNSUPPORTED_DESTINATION");
    expect(err.message).toBe("no electronic path");
    expect(err.details["destination"]).toBe("fcc");
    expect(err.requestId).toBe("r-1");
  });

  it("falls back to the FastAPI detail shape", () => {
    const err = parseErrorEnvelope(401, { detail: "Not authenticated" });
    expect(err.code).toBe("HTTP_401");
    expect(err.message).toBe("Not authenticated");
  });

  it("never throws on unparseable bodies", () => {
    for (const body of [null, undefined, "boom", 42, [], { error: "x" }]) {
      const err = parseErrorEnvelope(500, body);
      expect(err.status).toBe(500);
      expect(err.code).toBe("HTTP_500");
    }
  });

  it("defaults missing envelope fields", () => {
    const err = parseErrorEnvelope(409, { error: { details: "not an object" } });
    expect(err.code).toBe("HTTP_409");
    expect(err.message).toBe("Request failed");
    expect(err.details).toEqual({});
    expect(err.requestId).toBeNull();
  });

  it("describes errors for display", () => {
    expect(describeError(new ApiRequestError(404, "NOT_FOUND", "gone"))).toBe("gone (NOT_FOUND)");
    expect(describeError("x")).toBe("Something went wrong");
  });
});
