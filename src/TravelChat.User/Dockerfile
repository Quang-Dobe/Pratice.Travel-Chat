FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY Directory.Build.props Directory.Packages.props ./
COPY src/TravelChat.ServiceDefaults/TravelChat.ServiceDefaults.csproj src/TravelChat.ServiceDefaults/
COPY src/TravelChat.User/TravelChat.User.csproj src/TravelChat.User/
RUN dotnet restore src/TravelChat.User/TravelChat.User.csproj
COPY src/TravelChat.ServiceDefaults/ src/TravelChat.ServiceDefaults/
COPY src/TravelChat.User/ src/TravelChat.User/
RUN dotnet publish src/TravelChat.User/TravelChat.User.csproj -c Release --no-restore -o /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/publish .
USER app
EXPOSE 8080
ENTRYPOINT ["dotnet", "TravelChat.User.dll"]
