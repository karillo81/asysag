FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
# npm install (not `npm ci`) so the build tolerates a package-lock that is
# slightly out of sync with package.json — the deploy box hit that with ci.
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
