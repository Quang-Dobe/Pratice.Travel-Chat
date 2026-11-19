using Microsoft.AspNetCore.Mvc;

namespace TravelChat.Gateway.Features.Api;

public static class ApiProblems
{
    public static ValueTask WriteAsync(HttpContext http, int status)
    {
        http.Response.StatusCode = status;
        return http.RequestServices.GetRequiredService<IProblemDetailsService>().WriteAsync(new ProblemDetailsContext
        {
            HttpContext = http,
            ProblemDetails = new ProblemDetails { Status = status },
        });
    }
}
